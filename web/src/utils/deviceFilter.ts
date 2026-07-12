import type { AIPhoneDevice } from '../types/case'

// AI Phone 真机按部门/名称筛选：纯前端、按浏览器本地缓存，首页与快速页共用同一个 key 保持一致。
// 被过滤掉的设备不会进入设备池、不参与调度（等价于未勾选）。
const AIPHONE_DEVICE_FILTER_KEY = 'caseFlow.aiPhoneDeviceFilter'

export function readAiPhoneDeviceFilter(): string {
  try {
    return localStorage.getItem(AIPHONE_DEVICE_FILTER_KEY) ?? ''
  } catch {
    return ''
  }
}

export function persistAiPhoneDeviceFilter(value: string): void {
  try {
    if (value.trim()) {
      localStorage.setItem(AIPHONE_DEVICE_FILTER_KEY, value)
    } else {
      localStorage.removeItem(AIPHONE_DEVICE_FILTER_KEY)
    }
  } catch {
    /* localStorage 不可用时忽略，仅当次内存生效 */
  }
}

function deviceName(device: AIPhoneDevice): string {
  return String(device.alias || device.serial || '').trim()
}

// 关键字为空 → 全部返回；否则按设备名（alias/serial）做大小写不敏感的“包含”匹配。
export function filterAiPhoneDevices(devices: AIPhoneDevice[], keyword: string): AIPhoneDevice[] {
  const kw = keyword.trim().toLowerCase()
  if (!kw) {
    return devices
  }
  return devices.filter((device) => deviceName(device).toLowerCase().includes(kw))
}
