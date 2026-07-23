### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling a complete allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two fund-impacting failure modes: (1) if the router is allowlisted, any unprivileged user bypasses the curated-pool restriction entirely; (2) if the router is not allowlisted, individually allowlisted users are permanently blocked from using the router, breaking the core swap flow.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`sender` is populated by `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap()` call:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(
    address sender,   // ← this is msg.sender of pool.swap()
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`. Inside the pool, `msg.sender` is the **router address**, so `sender` forwarded to the extension is the router, not the end user.

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Failure mode A — bypass (router is allowlisted):**  
A pool admin who allowlists the router address (a natural step if they want all router-mediated swaps to work) inadvertently opens the pool to every user. Any address can call `MetricOmmSimpleRouter` and the extension will pass because `allowedSwapper[pool][router] == true`.

**Failure mode B — broken core flow (router is not allowlisted):**  
A pool admin allowlists specific user addresses. Those users attempt to swap through the router (the primary periphery entry point). The extension sees the router address, finds it not allowlisted, and reverts with `NotAllowedToSwap`. Allowlisted users are permanently unable to use the router; they must call the pool directly, which requires them to implement the `IMetricOmmSwapCallback` interface themselves.

The `DepositAllowlistExtension` does not share this exact problem because it gates on `owner` (the position recipient), which is explicitly passed by the caller and is not overwritten by the router. The swap path has no equivalent forwarding mechanism. [3](#0-2) 

---

### Impact Explanation

**Failure mode A** is a complete allowlist bypass on a curated pool. Any unprivileged user can trade on a pool the admin intended to restrict, potentially extracting value at oracle-anchored prices that were only meant to be available to vetted counterparties. This satisfies the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" criterion.

**Failure mode B** renders the core swap flow unusable for allowlisted users. The router is the only supported periphery swap entry point; users who cannot implement `IMetricOmmSwapCallback` are permanently locked out. This satisfies "Broken core pool functionality causing loss of funds or unusable swap flows."

---

### Likelihood Explanation

The router is the primary and intended way end users interact with pools. Any pool that deploys `SwapAllowlistExtension` and expects users to swap through `MetricOmmSimpleRouter` will immediately hit failure mode B. Failure mode A is reachable whenever an admin allowlists the router address, which is a natural configuration mistake given the router is the official periphery contract. No special privileges, flash loans, or exotic token behavior are required.

---

### Recommendation

The extension must check the **original user's address**, not the intermediary router's address. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change in the router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the economically relevant actor is the recipient of the output tokens. The `beforeSwap` hook already receives `recipient` as its second argument. Gating on `recipient` is router-transparent because the router passes the user-supplied recipient directly.

3. **Dedicated router-aware allowlist**: Maintain a separate allowlist for approved routers, and when `sender` is an approved router, check `recipient` instead.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow all router-mediated swaps.
3. Attacker (address NOT in allowedSwapper[pool][attacker]) calls
   MetricOmmSimpleRouter.exactInputSingle(...) targeting the pool.
4. Router calls pool.swap(recipient, ...).
   Inside pool: msg.sender = router.
   _beforeSwap(router, ...) is called.
   Extension checks allowedSwapper[pool][router] == true → passes.
5. Attacker's swap executes on the curated pool despite never being allowlisted.
```

Alternatively, for failure mode B:
```
1. Pool admin calls setAllowedToSwap(pool, user1, true).
2. user1 calls MetricOmmSimpleRouter.exactInputSingle(...).
3. Router calls pool.swap(...); msg.sender = router.
4. Extension checks allowedSwapper[pool][router] == false → reverts NotAllowedToSwap.
5. user1 is permanently blocked from using the router despite being allowlisted.
```

### Citations

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
