import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Fault } from '@waterguard/contracts'
import { clock, num } from '../format'
import type { CandidateView } from '../types'

echarts.use([LineChart, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

const LINE_COLORS = ['#7d8f89', '#14776b', '#b55b26', '#3f6fa8']

interface Props {
  candidates: CandidateView[]
  activeKey: string
  nodeId: string | null
  threshold: number
  warmupEnd: number
  fault: Fault | null
  time: number
  end: number
}

export function NodeChart(p: Props) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!el.current) return
    const c = echarts.init(el.current, undefined, { renderer: 'canvas' })
    chart.current = c
    const resize = () => c.resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el.current)
    window.addEventListener('resize', resize)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', resize)
      c.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    const c = chart.current
    if (!c) return
    if (!p.nodeId) {
      c.clear()
      return
    }
    const areas: unknown[] = [
      [{ xAxis: 0, itemStyle: { color: 'rgba(101,120,116,0.08)' } }, { xAxis: p.warmupEnd }],
    ]
    if (p.fault) {
      areas.push([
        { xAxis: p.fault.start_s, itemStyle: { color: 'rgba(180,61,57,0.08)' } },
        { xAxis: Math.min(p.fault.end_s, p.end) },
      ])
    }
    const series = p.candidates
      .filter((cand) => cand.series)
      .map((cand, i) => {
        const active = cand.key === p.activeKey
        const color = LINE_COLORS[i % LINE_COLORS.length]
        const data = cand.series!.frames.map((f) => {
          const n = f.nodes.find((x) => x.node_id === p.nodeId)
          return [f.time_s, n ? Number(n.pressure_m.toFixed(3)) : null]
        })
        return {
          name: cand.label,
          type: 'line',
          step: 'start',
          data,
          symbol: 'circle',
          symbolSize: active ? 6 : 4,
          z: active ? 3 : 2,
          lineStyle: { width: active ? 2.6 : 1.6, type: active ? 'solid' : 'dashed', color },
          itemStyle: { color },
          ...(i === 0
            ? {
                markArea: { silent: true, data: areas },
                markLine: {
                  silent: true,
                  symbol: 'none',
                  data: [
                    {
                      yAxis: p.threshold,
                      lineStyle: { color: '#b43d39', type: 'dashed', width: 1.4 },
                      label: { formatter: `阈值 ${num(p.threshold, 0)} m`, color: '#b43d39', position: 'insideEndTop' },
                    },
                    {
                      xAxis: p.time,
                      lineStyle: { color: '#183532', type: 'solid', width: 1 },
                      label: { formatter: clock(p.time), color: '#183532', position: 'insideEndTop' },
                    },
                  ],
                },
              }
            : {}),
        }
      })

    const option: EChartsCoreOption = {
      animation: false,
      grid: { left: 52, right: 18, top: 40, bottom: 34 },
      legend: { top: 4, left: 0, itemWidth: 18, textStyle: { color: '#183532', fontSize: 12 } },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? `${num(v, 2)} m` : '—'),
        axisPointer: { type: 'line' },
      },
      xAxis: {
        type: 'value',
        min: 0,
        max: p.end,
        interval: 1800,
        axisLabel: { formatter: (v: number) => clock(v), color: '#657874' },
        axisLine: { lineStyle: { color: '#c9d1c6' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        name: '压力（m）',
        nameTextStyle: { color: '#657874', align: 'left' },
        axisLabel: { color: '#657874' },
        splitLine: { lineStyle: { color: '#e4e9e1' } },
      },
      series,
    }
    c.setOption(option, true)
  }, [p.candidates, p.activeKey, p.nodeId, p.threshold, p.warmupEnd, p.fault, p.time, p.end])

  return (
    <div className="chart-wrap">
      <div className="chart" ref={el} role="img" aria-label={p.nodeId ? `节点 ${p.nodeId} 的压力曲线` : '尚未选择节点'} />
      {!p.nodeId && <div className="chart-empty">在管网图或违规列表里选一个节点，这里显示它的压力曲线</div>}
    </div>
  )
}
