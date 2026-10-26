"""PyTorch Geometric pressure-residual GNN with graph-physics and PDD losses."""
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GCNConv

from .compiler import assets


class PressureGNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = GCNConv(8, 32)
        self.conv2 = GCNConv(32, 32)
        self.output = nn.Linear(32, 1)

    def forward(self, x, edge_index, edge_weight):
        x = F.silu(self.conv1(x, edge_index, edge_weight))
        x = F.dropout(x, p=.1, training=self.training)
        x = F.silu(self.conv2(x, edge_index, edge_weight))
        return self.output(x).squeeze(-1)


class PhysicsResidualSurrogate:
    def __init__(self, task):
        self.task = task
        self.nodes = list(task['hard_constraints']['node_ids'])
        indexes = {n:i for i,n in enumerate(self.nodes)}
        wn = assets(task['network_ref']['network_id'])[0]
        weights = np.zeros((len(self.nodes),len(self.nodes)))
        fault = task['fault']
        excluded = set(fault.get('boundary_link_ids',[fault['link_id']]))
        for name, pipe in wn.pipes():
            a,b = pipe.start_node_name,pipe.end_node_name
            if name not in excluded and a in indexes and b in indexes:
                conductance = pipe.diameter**4.871*pipe.roughness**1.852/max(pipe.length,1.)
                weights[indexes[a],indexes[b]] += conductance
                weights[indexes[b],indexes[a]] += conductance
        edges = np.asarray(np.nonzero(weights))
        self.edge_index = torch.as_tensor(edges, dtype=torch.long)
        values = weights[edges[0],edges[1]]
        self.edge_weight = torch.as_tensor(values/max(float(values.mean()) if len(values) else 0,1e-12),dtype=torch.float32)
        self.pressure_scale = max(task['hydraulic_options']['required_pressure_m'],1.)
        elevation = np.array([wn.get_node(n).elevation for n in self.nodes])/self.pressure_scale
        demand = np.array([wn.get_node(n).base_demand for n in self.nodes])
        demand /= max(float(demand.max()),1e-12)
        degree = (weights>0).sum(axis=1).astype(float)
        degree /= max(float(degree.max()),1.)
        self.static = torch.tensor(np.column_stack([elevation,demand,degree]),dtype=torch.float32)
        local = task.get('physical_parameters',{}).get('node_pdd',{})
        options = task['hydraulic_options']
        self.minimum = torch.tensor([local.get(n,{}).get('minimum_pressure_m',options['minimum_pressure_m']) for n in self.nodes])
        self.required = torch.tensor([local.get(n,{}).get('required_pressure_m',options['required_pressure_m']) for n in self.nodes])
        self.exponent = torch.tensor([local.get(n,{}).get('pressure_exponent',options['pressure_exponent']) for n in self.nodes])
        with torch.random.fork_rng():
            torch.manual_seed(20261018)
            self.model = PressureGNN()
        self.optimizer = torch.optim.AdamW(self.model.parameters(),lr=.008,weight_decay=.001)
        self.training_pairs = 0
        self.residual_scale = None

    def features(self, point):
        d = point['demand_multiplier']/self.task['hydraulic_options']['demand_multiplier']
        f = self.task['fault']
        duration = (point['repair_end_s']-f['start_s'])/(self.task['time_window']['end_s']-f['start_s'])
        return np.array([1.,d-1,d**1.852-1,duration])

    def tensors(self, point):
        low = torch.tensor([point['low_fidelity']['node_pressure_m'][n] for n in self.nodes],dtype=torch.float32)
        scenario = torch.tensor(self.features(point),dtype=torch.float32).expand(len(self.nodes),-1)
        return torch.cat([low[:,None]/self.pressure_scale,self.static,scenario],dim=1),low

    def pdd(self, pressure):
        ratio=torch.clamp((pressure-self.minimum)/(self.required-self.minimum),1e-6,1).pow(self.exponent)
        return torch.where(pressure<=self.minimum,torch.zeros_like(ratio),ratio)

    def fit(self, points):
        pairs = [p for p in points if p['state'] not in ('unknown','invalid') and
                 p.get('low_fidelity',{}).get('state') != 'invalid' and
                 p.get('node_pressure_m') and p.get('low_fidelity',{}).get('node_pressure_m')]
        if len(pairs)<2 or len(pairs)==self.training_pairs:
            self.training_pairs = len(pairs)
            return
        self.training_pairs = len(pairs)
        examples=[]
        for p in pairs:
            x,low=self.tensors(p)
            target=torch.tensor([p['node_pressure_m'][n] for n in self.nodes],dtype=torch.float32)
            examples.append((x,low,target))
        self.model.train()
        for _ in range(40):
            self.optimizer.zero_grad()
            loss=torch.zeros(())
            for x,low,target in examples:
                correction=self.model(x,self.edge_index,self.edge_weight)*self.pressure_scale
                prediction=low+correction
                data_loss=F.mse_loss(prediction/self.pressure_scale,target/self.pressure_scale)
                pdd_loss=F.mse_loss(self.pdd(prediction),self.pdd(target))
                edge_delta=(correction[self.edge_index[0]]-correction[self.edge_index[1]])/self.pressure_scale
                graph_loss=(self.edge_weight*edge_delta.square()).sum()/max(self.edge_weight.numel(),1)
                loss=loss+data_loss+.1*pdd_loss+.01*graph_loss
            (loss/len(examples)).backward()
            self.optimizer.step()
        self.model.eval()
        with torch.no_grad():
            errors=torch.cat([low+self.model(x,self.edge_index,self.edge_weight)*self.pressure_scale-target for x,low,target in examples])
            self.residual_scale=max(.05,float(errors.square().mean().sqrt()))

    def predict(self, point):
        x,low = self.tensors(point)
        if self.training_pairs>=2:
            self.model.train()
            with torch.no_grad():
                draws=torch.stack([low+self.model(x,self.edge_index,self.edge_weight)*self.pressure_scale for _ in range(12)])
                pressure=draws.mean(dim=0)
                uncertainty=float(draws.std(dim=0).max())+self.residual_scale
            self.model.eval()
        else:
            pressure=low
            uncertainty=1.
        supply=self.pdd(pressure)
        gate=self.task['hard_constraints']
        required_head=self.minimum+(self.required-self.minimum)*gate['min_supply_ratio']**(1/self.exponent)
        margin=float((pressure-torch.maximum(required_head,torch.full_like(required_head,gate['min_pressure_m']))).min())
        return dict(min_pressure_m=float(pressure.min()),min_supply_ratio=float(supply.min()),
            uncertainty_m=float(uncertainty),margin_m=margin,
            acquisition_score=float(uncertainty/(abs(margin)+.2)),
            model='pyg-physics-residual-gnn/PDD-v1',training_pairs=self.training_pairs,
            uncertainty_kind='mc_dropout_plus_training_residual_not_confidence_interval')

    def describe(self):
        return dict(model='pyg-physics-residual-gnn/PDD-v1',library='torch_geometric.nn.GCNConv',training_pairs=self.training_pairs,
            hidden_channels=32,epochs_per_update=40,residual_scale_m=self.residual_scale,
            physics=['Hazen-Williams weighted graph','demand^1.852 feature','PDD loss and supply mapping','graph residual regularization'],
            uncertainty_kind='mc_dropout_plus_training_residual_not_confidence_interval')

    def save(self, path):
        torch.save(dict(model=self.model.state_dict(),nodes=self.nodes,training_pairs=self.training_pairs,
            residual_scale=self.residual_scale,description=self.describe()),path)
