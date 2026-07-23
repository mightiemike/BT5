### Title
`SwapAllowlistExtension` Checks Intermediary Caller Instead of End-User, Allowing Complete Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end-user. If the pool admin allowlists the router (required for router-based swaps to function), every user — including those not individually permitted — can bypass the per-user allowlist entirely.

---

### Finding Description

**Call chain when a user swaps via the router:**

```
User → router.exactInputSingle(...)
         → pool.swap(recipient, ...)          [msg.sender in pool = router]
             → _beforeSwap(msg.sender, ...)   [sender = router]
                 → extension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]   ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates: [2](#0-1) 

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()` — the router when going through `MetricOmmSimpleRouter.exactInputSingle`: [3](#0-2) 

The router calls `pool.swap(...)` directly, so `sender` arriving at the extension is always the router address, never the originating user.

**Two failure modes arise:**

| Router allowlisted? | Effect |
|---|---|
| Yes (to enable router swaps) | **Any user bypasses the per-user allowlist** by routing through the router |
| No | **All individually-allowlisted users are blocked** from using the router |

There is no configuration that simultaneously (a) allows router-based swaps and (b) enforces per-user allowlist restrictions.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner explicitly passed by the caller), not `sender`: [4](#0-3) 

Because `owner` is an explicit argument the router can set to the actual user, the deposit allowlist is not affected. The swap allowlist has no equivalent explicit user-identity argument.

**The `multicall` path compounds this:** `MetricOmmSimpleRouter.multicall` uses `delegatecall`, which preserves `msg.sender` inside the router, but the router still calls `pool.swap()` as itself: [5](#0-4) 

So a user batching multiple swaps via `multicall` still presents the router address to the extension for every hop.

---

### Impact Explanation

A pool admin deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or regulatory-compliant wallets) cannot simultaneously allow those users to use the standard router. If the router is allowlisted to unblock legitimate users, the allowlist is rendered inoperative: any address can call `router.exactInputSingle(...)` and the extension will pass because `allowedSwapper[pool][router] == true`. The admin-configured access boundary is completely bypassed by an unprivileged path.

---

### Likelihood Explanation

- Pools using `SwapAllowlistExtension` for access control are the intended production use case of that extension.
- Pool admins will naturally allowlist the canonical `MetricOmmSimpleRouter` to enable standard UX — this is the expected operational pattern.
- No special privilege or malicious setup is required; any user with a wallet can call the router.
- The bypass is silent: no revert, no event distinguishing router-mediated swaps from direct swaps.

---

### Recommendation

The `beforeSwap` hook must receive the true end-user identity. Two options:

1. **Preferred — explicit user field in `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap`. The extension decodes and checks this field. This requires a convention between router and extension but does not change the core interface.

2. **Alternative — check `recipient` instead of `sender`:** For swap allowlists the intent is often to restrict who *receives* output. Checking `recipient` (the second argument to `beforeSwap`) would be router-transparent because the router passes the actual user as `recipient`. However, this does not cover cases where the user wants to send output to a third party.

The `DepositAllowlistExtension` pattern (checking an explicit `owner` argument) is the correct model; `SwapAllowlistExtension` should adopt an equivalent explicit user-identity argument rather than relying on `sender`.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool is deployed with SwapAllowlistExtension
// 2. Pool admin allowlists the router: allowedSwapper[pool][router] = true
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

// Verify attacker is not individually allowed:
assert(!extension.isAllowedToSwap(address(pool), attacker));

// Attacker bypasses allowlist via router:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: tokenIn,
        tokenOut: tokenOut,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds — extension checked allowedSwapper[pool][router] == true,
// never checked allowedSwapper[pool][attacker] == false.
```

The extension's `beforeSwap` receives `sender = address(router)`, looks up `allowedSwapper[pool][router]`, finds `true`, and returns the success selector. The attacker's identity is never evaluated.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
