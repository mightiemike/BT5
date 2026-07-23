### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][endUser]`. A pool admin who allowlists the router to support standard periphery access inadvertently opens the gate to every user on-chain, completely defeating the allowlist.

---

### Finding Description

**Call chain when routing through `MetricOmmSimpleRouter`:**

```
user → router.exactInputSingle(pool, ...) 
     → pool.swap(recipient, ...) [msg.sender = router]
       → _beforeSwap(msg.sender=router, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
           → allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

The router is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` on it: [4](#0-3) 

**The inescapable dilemma for pool admins:**

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user on-chain can bypass the allowlist via the router |
| Do not allowlist the router | Even allowlisted users cannot use the standard periphery |

There is no configuration that simultaneously supports router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or whitelisted market makers). If the pool admin allowlists the router — a natural step to support the standard periphery — any unprivileged user can call `router.exactInputSingle(pool, ...)` and trade against the pool's LP liquidity at oracle prices. LP funds are directly at risk because the pool's curation policy is silently voided. The attacker pays nothing beyond gas; the LP bears the full economic exposure of trades they never consented to allow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery entry point for swaps. Pool admins who want their allowlisted users to have a normal UX will allowlist the router. The bypass is then reachable by any address with no special privileges, no malicious setup, and no non-standard tokens. The only precondition is that the pool admin has allowlisted the router, which is the expected operational pattern.

---

### Recommendation

The extension must check the **end user** identity, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` only when `sender` is not a known router, and require routers to forward the real user**: Add a `IMetricOmmRouter` interface that exposes the current end user, and have the extension query it when `sender` is a registered router.

3. **Simplest fix**: Change the extension to check `msg.sender` of the extension call (the pool) against a registry that maps `pool → allowedSwapper`, but also require the pool to pass the **original EOA** as `sender`. This requires the pool to unwrap the router layer — which is only possible if the router explicitly forwards the user identity.

The root fix is that `sender` in `beforeSwap` must always be the economically responsible party, not the contract that called `pool.swap()`.

---

### Proof of Concept

```solidity
// Setup:
// 1. Pool admin deploys pool with SwapAllowlistExtension
// 2. Admin allowlists alice directly: setAllowedToSwap(pool, alice, true)
// 3. Admin allowlists the router (to support periphery): setAllowedToSwap(pool, router, true)

// Attack: bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob); // bob is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000_000e18,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ✓ Swap succeeds: extension saw sender=router (allowlisted), not bob (not allowlisted)
// LP funds drained at oracle price by an unauthorized counterparty
```

The pool's `swap()` receives `msg.sender = router`, passes `sender = router` to the extension, and the extension approves because `allowedSwapper[pool][router] == true`. [5](#0-4) [1](#0-0) [6](#0-5)

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
