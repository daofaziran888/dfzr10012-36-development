import { useCallback, useEffect, useState } from 'react'

import { fetchJson, request } from '@/api/client'

const ENDPOINT = '/api/bpsample'
const columns = [
  '结果编号',
  '指标编号',
  '阻塞算子',
  '缓冲区水位',
  '消费延迟',
  '采样时间',
  '严重级别',
  '建议并行度',
  '告警待办',
  '采样版本',
  '处理状态',
]
const todoColumns = ['待办编号', '指标编号', '阻塞算子', '严重级别', '结果编号', '采样版本', '待办状态']
const severities = ['正常', '轻微', '严重', '已恢复']

type Row = Record<string, string | number | null>
type Card = { label: string; value: number }

export default function BpsampleView() {
  const [rows, setRows] = useState<Row[]>([])
  const [todos, setTodos] = useState<Row[]>([])
  const [cards, setCards] = useState<Card[]>([])
  const [maxVersion, setMaxVersion] = useState(0)
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [severity, setSeverity] = useState('')
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (severity) query.set('severity', severity)
    try {
      const [listPayload, todoPayload, overviewPayload] = await Promise.all([
        fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}?${query.toString()}`),
        fetchJson<{ items: Row[] }>(`${ENDPOINT}/todos`),
        fetchJson<{ cards: Card[]; 最高采样版本: number }>(`${ENDPOINT}/overview`),
      ])
      setRows(listPayload.items ?? [])
      setTotal(listPayload.total ?? 0)
      setTodos(todoPayload.items ?? [])
      setCards(overviewPayload.cards ?? [])
      setMaxVersion(overviewPayload.最高采样版本 ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '反压采样数据结果读取失败')
    }
  }, [keyword, severity])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runSample(metricNo?: string) {
    setMessage('')
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/sample`, {
        method: 'POST',
        body: JSON.stringify({ values: metricNo ? { 指标编号: metricNo } : {} }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || '反压采样未生效，请稍后重试')
      }
      setMessage(payload.message ?? '反压采样完成')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '反压采样操作失败')
    }
  }

  return (
    <section className="page" data-module="bpsample">
      <header className="page-head">
        <div>
          <h2>反压采样数据结果</h2>
          <p className="page-desc">
            读取反压指标与告警规则，校验严重级别后写入采样结果，保留建议并行度、告警待办和采样版本；同一算子只保留最新版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => void runSample()}>
            执行全量反压采样
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出采样结果清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {cards.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
        <article className="stat-card">
          <span className="stat-label">最高采样版本</span>
          <strong className="stat-value">v{maxVersion}</strong>
        </article>
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void reload()
        }}
      >
        <label className="filter-item">
          <span>指标编号 / 阻塞算子</span>
          <input
            value={keyword}
            placeholder="按指标编号或阻塞算子检索"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </label>
        <label className="filter-item">
          <span>严重级别</span>
          <select value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="">全部</option>
            {severities.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => {
            setKeyword('')
            setSeverity('')
          }}
        >
          重置条件
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column}>{column === '处理状态' ? row.status ?? '—' : row[column] ?? '—'}</td>
              ))}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void runSample(String(row['指标编号'] ?? ''))}>
                  重新采样
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>
                暂无反压采样数据结果，可先执行全量反压采样
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h3>告警待办（按最终确认的采样结果校验）</h3>
      <table className="data-table">
        <thead>
          <tr>
            {todoColumns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {todos.map((todo) => (
            <tr key={String(todo.id)}>
              {todoColumns.map((column) => (
                <td key={column}>{column === '待办状态' ? todo.status ?? '—' : todo[column] ?? '—'}</td>
              ))}
            </tr>
          ))}
          {todos.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={todoColumns.length}>
                暂无告警待办，严重反压采样后会自动生成且只生成一条
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条反压采样数据结果</span>
        {message && <span>{message}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
