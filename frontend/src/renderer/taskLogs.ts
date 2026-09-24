export function collectTaskLogs(
  tasks: { task_id: string; log_lines: string[] }[],
  previous: Record<string, number>,
) {
  const cursors = { ...previous }
  const lines: string[] = []
  for (const task of tasks) {
    const logs = task.log_lines || []
    const cursor = cursors[task.task_id] || 0
    lines.push(...logs.slice(cursor))
    cursors[task.task_id] = Math.max(cursor, logs.length)
  }
  return { cursors, lines }
}
