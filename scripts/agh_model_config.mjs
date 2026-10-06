// Reuse the reviewed AGH account only for the same provider and endpoint.
export function resolveModelConfig(config, snapshot) {
  if (config.accountId) return { ...config }
  const endpoint = url => url.replace(/\/$/, '')
  const matches = (snapshot.accounts || []).filter(account =>
    account.providerId === config.providerId && account.enabled && account.credentialConfigured &&
    (!config.baseUrl || endpoint(account.baseUrl) === endpoint(config.baseUrl)))
  const account = matches.find(row => row.accountId === snapshot.defaultAccountId) ||
    (matches.length === 1 ? matches[0] : undefined)
  return { ...config, ...(account ? { accountId: account.accountId } : {}) }
}
