### Title
SwapAllowlistExtension Checks Router Address Instead of End-User — Swap Allowlist Bypassed via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the router is allowlisted on a curated pool (which is required for any router-based swap to succeed), every user — including those explicitly denied — can bypass the per-user swap allowlist by calling the pool through the router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding `msg.sender` as the `sender` argument to the extension. [1](#0-0) 

`_beforeSwap` in `ExtensionCalling.sol` passes this value directly to `IMetricOmmExtensions.beforeSwap` as the first argument. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter` (a supported periphery path), the router calls `pool.swap(...)` with itself as `msg.sender`. The extension therefore sees `sender = address(router)`, not the end user. If the pool admin allowlists the router address (which is necessary for any router-based swap to work on a curated pool), the allowlist check passes for **every** user who routes through it, regardless of whether that individual user is permitted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner), which is the economically attributed actor and is not substituted by the router. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Disallowed users can execute swaps, draining LP value or violating regulatory/compliance constraints the pool admin intended to enforce. This is a direct loss of curation policy with fund-impacting consequences for LPs on restricted pools.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard supported periphery path documented for end users. Any pool admin who allowlists the router (a natural operational step) unknowingly opens the bypass. The trigger requires no privilege — any unpermissioned user can call the router. The misconfiguration is latent in the design of the actor-binding, not in any unusual setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end user**, not the immediate caller of the pool. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes the true end-user address in `extensionData`, and the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` instead of `sender`**: If the pool's swap semantics guarantee `recipient` is always the economic beneficiary, gate on `recipient`. Verify this holds for all router paths.
3. **Require direct pool calls for curated pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory/extension initialization level.

The cleanest fix is option 1 with a verified encoding, or redesigning the hook signature to carry a separate `originator` field that the pool populates from a trusted source.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists `address(router)` via `setAllowedToSwap(pool, address(router), true)` to enable normal router usage.
3. Pool admin does **not** allowlist `address(attacker)`.
4. Attacker calls `MetricOmmSimpleRouter.swap(pool, ...)` targeting the curated pool.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = address(router)`.
6. `_beforeSwap` fires with `sender = address(router)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][address(router)] == true` → passes.
8. Attacker's swap executes on the curated pool despite being individually denied. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
