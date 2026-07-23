### Title
`SwapAllowlistExtension` gates on the direct pool caller (`sender`) instead of the economic actor, allowing any user to bypass per-user swap restrictions through an allowlisted router — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. If the router is allowlisted for a curated pool (the only way to support router-based swaps on that pool), every user — including those explicitly excluded from the per-user allowlist — can trade freely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." [1](#0-0) 

Its `beforeSwap` hook receives `sender` (the direct caller of the pool's `swap()`) and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

The pool's `_beforeSwap` internal function passes `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument forwarded to every extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(recipient=user, ...)`. At that point `msg.sender` to the pool is the **router**, so `sender` delivered to the extension is the **router address**, not the end user. The allowlist check becomes:

```
allowedSwapper[pool][router]   // router is the actor checked, not the user
```

For a curated pool to support any router-based swap at all, the pool admin must allowlist the router. Once the router is allowlisted, the per-user allowlist is completely bypassed: every user, including those explicitly excluded, can swap by routing through the router.

The `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the position owner), which is the economic actor regardless of who the intermediary caller is: [4](#0-3) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) loses that restriction entirely for any user who routes through the official router. Disallowed users can execute swaps, drain LP value, or trade against oracle prices that the pool admin intended to expose only to trusted parties. This is a direct loss of curation control and a high-severity policy bypass on every pool that pairs `SwapAllowlistExtension` with router support.

---

### Likelihood Explanation

The scenario requires:
1. A pool configured with `SwapAllowlistExtension` (a supported production extension).
2. The pool admin having allowlisted the router so that legitimate allowlisted users can trade via the router (the normal operational assumption).

Both conditions are routine. Any pool that wants to support the official periphery router while also restricting individual swappers will inevitably allowlist the router, triggering the bypass. No privileged attacker capability is needed — any user with a standard router call can exploit it.

---

### Recommendation

Replace the `sender` check with a check on the **recipient** (the economic beneficiary of the swap), or introduce a separate `swapper` identity field that the router populates from its own `msg.sender` via `extensionData`. The simplest fix consistent with the existing interface is to check `recipient` instead of `sender`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, mirror the `DepositAllowlistExtension` pattern: gate on the actor who receives the economic benefit (`recipient`) rather than the intermediary who submits the transaction (`sender`).

---

### Proof of Concept

**Setup:**
- Pool is deployed with `SwapAllowlistExtension` as `extension1`.
- Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
- Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
- Bob is **not** allowlisted.

**Attack:**
1. Bob calls `MetricOmmSimpleRouter.exactInputSingle(pool, ..., recipient=bob, ...)`.
2. Router calls `pool.swap(recipient=bob, ...)` — `msg.sender` to the pool is the router.
3. Pool calls `extension.beforeSwap(sender=router, recipient=bob, ...)`.
4. Extension checks `allowedSwapper[pool][router]` → `true` (router is allowlisted).
5. Swap executes. Bob receives tokens despite being explicitly excluded from the allowlist.

**Expected:** revert `NotAllowedToSwap`.
**Actual:** swap succeeds. [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
