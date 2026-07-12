// 一次“点击执行”生成一个 execution_request_group_id，随本次拆出的多路 submit 一起带给后端，
// 让后端把同一次点击的调用日志（execution_strategy_call_logs）串起来（检查点 7）。
export function newExecutionRequestGroupId(): string {
  const cryptoObj = globalThis.crypto
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID()
  }
  return `grp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
