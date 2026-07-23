### Title
`DepositAllowlistExtension` Guards `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the `addLiquidity` call) and instead validates the `owner` argument (the LP-position beneficiary). Because `owner` is a free caller-supplied parameter, any address not on the allowlist can pass the guard by nominating any allowlisted address as `owner`, while the real caller — who supplies the tokens and triggers the hook — is never checked.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary:

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, …)` but discards `sender` (the `address,` wildcard) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`SwapAllowlistExtension.beforeSwap` — the parallel guard for swaps — correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The two guards are structurally identical in intent but check different identities: `sender` for swaps, `owner` for deposits. This is the direct analog of the external report's inconsistent validation: the same guard concept is applied in two hook contexts, but the identity being validated differs between them, breaking the invariant in one path.

---

### Impact Explanation

The deposit allowlist is an admin-configured access-control boundary. Its purpose is to restrict which addresses may supply liquidity to a pool (e.g., KYC/AML compliance, institutional-only pools, or protocol-controlled liquidity). Because the guard checks `owner` rather than `sender`:

- Any address not on the allowlist can call `addLiquidity(allowlistedAddress, salt, deltas, …)`, pass the guard, and inject liquidity into a restricted pool.
- The actual token transfer is executed by the unauthorized caller via the swap callback; the position is credited to the allowlisted `owner`.
- The pool admin's access-control boundary is silently voided for every `addLiquidity` call where `owner` is allowlisted but `sender` is not.

This is an admin-boundary break: an unprivileged path bypasses an admin-configured extension guard, violating the invariant that only allowlisted addresses may deposit into a restricted pool.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an allowlisted `owner`. The allowlisted addresses are publicly readable from `allowedDepositor`. The attack is trivially constructable from on-chain data alone and is reachable through the standard `addLiquidity` entry point.

---

### Recommendation

Replace the discarded `address,` wildcard with a named `sender` parameter and check it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address `A`.
2. Unauthorized address `B` (not on the allowlist) calls:
   ```solidity
   pool.addLiquidity(A /*owner*/, salt, deltas, callbackData, extensionData);
   ```
3. The pool calls `DepositAllowlistExtension.beforeAddLiquidity(B /*sender*/, A /*owner*/, …)`.
4. The guard evaluates `allowedDepositor[pool][A]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls `B`'s callback to collect tokens.
6. The position is minted to `A`; `B` has successfully deposited into a pool it was explicitly excluded from.
7. The pool admin's allowlist invariant — "only allowlisted addresses may supply liquidity" — is broken without any privileged action. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
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
