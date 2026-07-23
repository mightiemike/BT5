### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` at the pool call boundary. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-based swaps for their curated users, the allowlist is rendered completely ineffective: any unprivileged user can bypass it by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
     → IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)
         // msg.sender at pool = router address
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
         // checks allowedSwapper[pool][router], NOT allowedSwapper[pool][user]
```

**Pool `swap` passes `msg.sender` as `sender` to the extension:**

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

**`ExtensionCalling._beforeSwap` forwards it verbatim:**

```solidity
// ExtensionCalling.sol:163-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)  // sender = router address
)
```

**`SwapAllowlistExtension.beforeSwap` checks the wrong actor:**

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct key), sender = router (wrong actor)
```

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the public router at all (broken UX).
- **Allowlist the router** → the allowlist is bypassed entirely; any user can swap through the router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

**Direct loss of curation policy / unauthorized swap execution on curated pools.**

Pools that deploy `SwapAllowlistExtension` are explicitly designed to restrict trading to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, whitelisted market makers). Once the router is allowlisted (the only way to enable router-based swaps for legitimate users), any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` targeting the curated pool.
2. The extension sees `sender = router` (allowlisted) and passes.
3. The swap executes against the pool's LP reserves at oracle-anchored prices.

This constitutes a broken core pool functionality (the allowlist guard fails open) and allows unauthorized users to drain LP value from a pool that was configured to restrict access. The impact is **High** — it directly breaks the access-control invariant that the pool admin configured, and any unauthorized swap that moves the pool's bin cursor extracts value from LPs.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary public periphery entry point for swaps. Any pool that:
1. Deploys with `SwapAllowlistExtension` (a supported, documented extension), AND
2. Wants its allowlisted users to be able to use the router (the normal UX path),

must allowlist the router, at which point the bypass is trivially reachable by any user with no special privileges. The attacker only needs to call a standard public router function.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end user), not the intermediary contract. Two approaches:

1. **Pass the original user through the router:** The router should forward the original `msg.sender` as a trusted `sender` field. This requires a protocol-level convention (e.g., the pool reads the original sender from transient storage set by the router, similar to how the callback context already works with `_setNextCallbackContext`).

2. **Gate on `recipient` instead of `sender`:** For swap allowlists, the economically relevant actor is the recipient of output tokens. The extension could check `allowedSwapper[pool][recipient]` instead. However, this only works if the pool admin intends to gate by recipient, not by payer.

The cleanest fix is option 1: store the original `msg.sender` in transient storage at router entry (alongside the existing callback context) and expose it to extensions via the `sender` argument, so the extension always sees the true initiating user regardless of intermediary.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `allowedUser` is allowlisted
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so that allowedUser can use it
ext.setAllowedToSwap(pool, address(router), true);
// allowedUser is also allowlisted for direct swaps
ext.setAllowedToSwap(pool, allowedUser, true);

// Attack: bannedUser (not allowlisted) swaps through the router
vm.prank(bannedUser);
token0.approve(address(router), type(uint256).max);

// This succeeds because the extension checks allowedSwapper[pool][router] = true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: bannedUser,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// bannedUser successfully swapped on a curated pool — allowlist bypassed
```

**Root cause chain:**
- `MetricOmmPool.swap` passes `msg.sender` (router) as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router, not the user [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making itself `msg.sender` at the pool level with no mechanism to forward the original user [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
