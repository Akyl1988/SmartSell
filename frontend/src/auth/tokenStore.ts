const REFRESH_TOKEN_KEY = 'smartsell_refresh_token'
const LEGACY_ACCESS_TOKEN_KEY = 'access_token'
const LEGACY_REFRESH_TOKEN_KEY = 'refresh_token'

let accessTokenInMemory: string | null = null
let bootstrapped = false

function safeGetSessionStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return window.sessionStorage
  } catch {
    return null
  }
}

function safeGetLocalStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return window.localStorage
  } catch {
    return null
  }
}

export function bootstrapTokenStore(): void {
  if (bootstrapped) return
  bootstrapped = true

  const sessionStorageRef = safeGetSessionStorage()
  const localStorageRef = safeGetLocalStorage()

  const legacyAccessToken = localStorageRef?.getItem(LEGACY_ACCESS_TOKEN_KEY) ?? null
  const legacyRefreshToken = localStorageRef?.getItem(LEGACY_REFRESH_TOKEN_KEY) ?? null

  if (legacyAccessToken) {
    accessTokenInMemory = legacyAccessToken
  }

  if (legacyRefreshToken && sessionStorageRef && !sessionStorageRef.getItem(REFRESH_TOKEN_KEY)) {
    sessionStorageRef.setItem(REFRESH_TOKEN_KEY, legacyRefreshToken)
  }

  localStorageRef?.removeItem(LEGACY_ACCESS_TOKEN_KEY)
  localStorageRef?.removeItem(LEGACY_REFRESH_TOKEN_KEY)
}

export function getAccessToken(): string | null {
  return accessTokenInMemory
}

export function getRefreshToken(): string | null {
  const storage = safeGetSessionStorage()
  return storage?.getItem(REFRESH_TOKEN_KEY) ?? null
}

export function hasSessionToken(): boolean {
  return Boolean(getAccessToken() || getRefreshToken())
}

export function setSessionTokens(accessToken: string, refreshToken: string): void {
  accessTokenInMemory = accessToken
  const storage = safeGetSessionStorage()
  storage?.setItem(REFRESH_TOKEN_KEY, refreshToken)
}

export function clearSessionTokens(): void {
  accessTokenInMemory = null
  const sessionStorageRef = safeGetSessionStorage()
  const localStorageRef = safeGetLocalStorage()

  sessionStorageRef?.removeItem(REFRESH_TOKEN_KEY)
  localStorageRef?.removeItem(LEGACY_ACCESS_TOKEN_KEY)
  localStorageRef?.removeItem(LEGACY_REFRESH_TOKEN_KEY)
}
