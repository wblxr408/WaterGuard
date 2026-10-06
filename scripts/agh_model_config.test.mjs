import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveModelConfig } from './agh_model_config.mjs'

const account = { accountId: 'agnes-ai', providerId: 'agnes-ai', baseUrl: 'https://api.agnes-ai.cn/v1', enabled: true, credentialConfigured: true }
const snapshot = { defaultAccountId: 'agnes-ai', accounts: [account] }

test('test and save without a new key address the persisted default account', () => {
  const config = { providerId: 'agnes-ai', baseUrl: 'https://api.agnes-ai.cn/v1/' }
  assert.equal(resolveModelConfig(config, snapshot).accountId, 'agnes-ai')
  assert.equal(config.accountId, undefined)
})

test('provider or endpoint changes do not reuse another account credential', () => {
  assert.equal(resolveModelConfig({ providerId: 'deepseek' }, snapshot).accountId, undefined)
  assert.equal(resolveModelConfig({ providerId: 'agnes-ai', baseUrl: 'https://other.invalid/v1' }, snapshot).accountId, undefined)
  assert.equal(resolveModelConfig({ providerId: 'agnes-ai' }, { accounts: [{ ...account, enabled: false }] }).accountId, undefined)
})

test('explicit account selection is preserved and ambiguous accounts are not guessed', () => {
  assert.equal(resolveModelConfig({ providerId: 'agnes-ai', accountId: 'chosen' }, snapshot).accountId, 'chosen')
  const accounts = [account, { ...account, accountId: 'another' }]
  assert.equal(resolveModelConfig({ providerId: 'agnes-ai' }, { accounts }).accountId, undefined)
  assert.equal(resolveModelConfig({ providerId: 'agnes-ai' }, { accounts, defaultAccountId: 'another' }).accountId, 'another')
})
