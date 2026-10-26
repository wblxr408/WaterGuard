import assert from 'node:assert/strict';
import {test} from 'node:test';
import * as data from '../src/data.ts';

const task={task_id:'task-a',fault:{start_s:3600,end_s:7200},hydraulic_options:{demand_multiplier:1.2},
 time_window:{step_s:900,end_s:10800},action_limits:{earliest_s:3600}};
const plan={candidate_id:'a',actions:[]};

test('boundary request uses current task demand and leaves sampling below full grid',()=>{
 const request=data.decisionStudyInput?.(task,plan,'boundary',{budget:5});
 assert.deepEqual(request?.demand_multipliers,[1.08,1.2,1.32,1.44,1.56]);
 assert.deepEqual(request.repair_end_times_s,[5400,6300,7200,8100,9000]);
 assert.equal(request.max_simulations,5);
 assert.equal(request.plan,plan);
});

test('rescue keeps user-declared cost and sends the selected operation',()=>{
 const action={link_id:'10',kind:'pump_on',simulation_time_s:5400};
 const request=data.decisionStudyInput?.(task,plan,'rescue',{budget:4,rescueOptions:[
  {label:'备用泵',cost:2,extra_action:action}],target:'basic'});
 assert.deepEqual(request?.rescue_options,[{label:'备用泵',cost:2,extra_action:action}]);
 assert.equal(request.rescue_target,'basic');
});

test('event request records actual event and response times',()=>{
 const request=data.decisionStudyInput?.(task,plan,'event',{budget:4,eventTime:5400,responseTime:600,repairEnd:9000});
 assert.equal(request?.event_time_s,5400);
 assert.equal(request.response_time_s,600);
 assert.equal(request.new_repair_end_s,9000);
});
