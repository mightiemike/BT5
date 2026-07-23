### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unauthorized depositor to bypass the allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller) and gates on `owner` (the LP position owner). Because `addLiquidity` lets any caller specify an arbitrary `owner`, an unauthorized depositor can bypass the allowlist entirely by naming any allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards the real caller as `sender` and the caller-supplied position owner as `owner` to the extension hook:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` passes both values verbatim to every registered extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` drops the first argument and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The admin-facing setter names its parameter `depositor`, confirming the intent is to restrict callers, not position owners:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

The `SwapAllowlistExtension` correctly checks `sender` (the actual caller), making the discrepancy in `DepositAllowlistExtension` clearly unintentional:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any address not in the allowlist can call `pool.addLiquidity(allowlistedAddress, ...)`, pass the `owner` check, and add liquidity to the pool. The unauthorized caller pays the tokens; the allowlisted address receives the LP shares. The pool admin's intended access control — e.g., restricting deposits to KYC'd LPs, preventing manipulation, or enforcing regulatory constraints — is silently bypassed by any unprivileged actor who knows any allowlisted address (all of which are observable on-chain from prior `setAllowedToDeposit` events).

---

### Likelihood Explanation

Exploitation requires no special privilege. Any external account can call `addLiquidity` with an arbitrary `owner`. Allowlisted addresses are discoverable from emitted `AllowedToDepositSet` events. The bypass is unconditional whenever the pool has a non-zero `beforeAddLiquidity` order pointing to this extension.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` registered in `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is permitted.
3. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Pool calls `_beforeAddLiquidity(bob, alice, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → hook returns success.
6. Bob's tokens are transferred into the pool; Alice's position receives the LP shares.
7. Bob has successfully deposited despite being absent from the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
