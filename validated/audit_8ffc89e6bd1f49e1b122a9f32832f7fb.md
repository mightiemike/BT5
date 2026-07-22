### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual user, allowing any unprivileged address to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the natural step to support normal user flows), every address — including non-allowlisted ones — can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
User (Bob, not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
          → _beforeSwap(msg.sender=router, recipient, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → checks allowedSwapper[pool][router]    // ← router, not Bob
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool — the router, not the end user: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making itself the pool's `msg.sender`: [4](#0-3) 

The same substitution occurs in `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [5](#0-4) 

**The structural dilemma this creates:**

| Pool admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot swap through the router at all — broken UX |
| **Does** allowlist the router | Every address can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously supports router-mediated swaps and enforces per-user allowlisting.

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a private institutional pool or a KYC-gated pool). To support normal user flows, the admin allowlists the router. Any non-allowlisted address can then call `MetricOmmSimpleRouter.exactInputSingle` and swap freely. The extension's guard is silently bypassed: the pool executes the swap, transfers tokens out to the attacker's `recipient`, and the callback pulls tokens from the attacker's approved balance. LP funds are drained through unauthorized trades that the allowlist was specifically configured to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to support normal user flows will allowlist the router as a matter of course — the documentation and test suite show the router as the standard entry point. The bypass requires no special privilege, no flash loan, and no multi-block setup: a single `exactInputSingle` call from any EOA suffices once the router is allowlisted.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router.** Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `sender` only for direct pool calls; require the router to forward the real user identity.** Add a dedicated field (e.g., `realSender`) to the extension data ABI that the router always populates with its own `msg.sender`, and have the extension prefer that field over the raw `sender` when the raw `sender` is a known router.

3. **Simplest fix:** Change the allowlist semantics so that the router is never allowlisted as a swapper; instead, require all users to call the pool directly when a swap allowlist is active, and document this constraint clearly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted as a swapper.
// Admin also allowlists the router so that alice can use it.
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← necessary for alice to use router

// Attack: bob (not allowlisted) swaps through the router.
// The extension sees sender = router (allowlisted) → passes.
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        recipient:       bob,
        tokenIn:         address(token0),
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// Bob receives token1 despite never being allowlisted.
// The SwapAllowlistExtension guard was silently bypassed.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
