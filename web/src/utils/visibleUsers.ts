import type { User } from '../types/case'

export const VISIBLE_USER_NAMES: string[] = []

const visibleUserNameSet = new Set(VISIBLE_USER_NAMES.map(normalizeUserName))

function normalizeUserName(value: string) {
  return value.replace(/\s+/g, '').toLowerCase()
}

export function filterVisibleUsers(users: User[]) {
  if (!visibleUserNameSet.size) {
    return users
  }
  return users.filter(
    (user) =>
      visibleUserNameSet.has(normalizeUserName(user.displayName)) ||
      visibleUserNameSet.has(normalizeUserName(user.name)),
  )
}
