### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

**Step 2 — Extension checks `sender` (the router), not the actual user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist, where `msg.sender` is the pool: [2](#0-1) 

The effective check is `allowedSwapper[pool][router]`.

**Step 3 — Router calls the pool directly, hiding the actual user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no mechanism to forward the original `msg.sender`: [3](#0-2) 

The pool's `msg.sender` is the router. The extension sees `sender = router`, not the actual user.

**Step 4 — The admin faces an impossible dilemma.**

- If the admin does **not** allowlist the router: individually allowlisted users who use the router are blocked (broken UX).
- If the admin **does** allowlist the router: every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users, because the router erases the caller's identity.

The same structural problem applies to multi-hop `exactInput` and `exactOutput` paths: [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd users, protocol partners). Any non-allowlisted user can bypass this control by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`) instead of calling the pool directly. The extension approves the swap because it sees the allowlisted router address as `sender`. The disallowed user receives output tokens from the pool, violating the pool's access policy. This constitutes a broken core pool functionality and a direct admin-boundary break — the allowlist guard is rendered completely ineffective for router-mediated swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery contract. Pool admins who want to support normal user flows must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any user can call `exactInputSingle` on a curated pool. Likelihood is **high** whenever a curated pool also permits router access.

---

### Recommendation

The extension must check the **economically relevant actor**, not the intermediary. Two approaches:

1. **Pass the original caller through the pool.** Add an optional `originator` field to the swap call or extension data, populated by the router with `msg.sender`, and have the extension verify it. This requires a coordinated interface change.

2. **Check `recipient` instead of `sender` for swap allowlisting**, if the pool's threat model is about who receives output (not who initiates). This is a simpler but semantically different gate.

3. **Require direct pool interaction for curated pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this in the extension's `beforeSwap` by reverting if `msg.sender` (the pool) is called from a known router — though this is fragile.

The cleanest fix is option 1: the router should forward `msg.sender` as `originator` in `extensionData`, and `SwapAllowlistExtension` should decode and check it.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can swap via router
extension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted
address alice = makeAddr("alice");
// Alice is not in allowedSwapper[pool][alice]

// Alice bypasses the allowlist by routing through the router
// The extension sees sender = address(router), which IS allowlisted
vm.prank(alice);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: alice,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Alice successfully swaps despite not being on the allowlist.
// The extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][alice].
``` [5](#0-4) [6](#0-5) [1](#0-0) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
