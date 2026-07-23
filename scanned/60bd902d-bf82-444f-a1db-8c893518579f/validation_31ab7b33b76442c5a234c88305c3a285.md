### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` at the pool level. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router address to enable router-mediated swaps, every user of the public router bypasses the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

When this executes, `msg.sender` at the pool is `address(router)`, so the extension receives `sender = router`. The extension has no visibility into the original `msg.sender` of the router call (the actual end-user).

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist the router address | Every user of the public router bypasses the allowlist |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

The bypass path is fully unprivileged: any user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` where `pool` has a `SwapAllowlistExtension` that allowlists the router.

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned bots). To allow those counterparties to use the standard router, the admin adds `address(router)` to the allowlist. At that point, any unprivileged address can call `MetricOmmSimpleRouter` and execute swaps against the restricted pool, draining LP assets at oracle-determined prices without authorization. The allowlist guard is completely neutralized.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, public, permissionless swap interface for the protocol. Any user who observes that a pool has a swap allowlist and that the router is allowlisted can immediately exploit this. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **end-user identity**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router or a signed payload.

2. **Check `sender` against the allowlist but also accept the router as a transparent forwarder only when the router itself encodes the real user**: Require the router to ABI-encode the real payer/user in `extensionData` and have the extension decode and check that address instead of `sender`.

3. **Require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call `pool.swap()` directly. This is a usage restriction, not a code fix, and is fragile.

The cleanest fix is option 1 or 2: the extension should read the real swapper from a verified field in `extensionData` rather than trusting the `sender` argument, which is corrupted by any intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    (to allow allowlisted users to use the router)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: restrictedPool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls restrictedPool.swap(attacker, true, X, ...)
     → pool's msg.sender = address(router)
  3. _beforeSwap(address(router), ...) is called
  4. SwapAllowlistExtension.beforeSwap receives sender = address(router)
  5. allowedSwapper[pool][router] == true → check passes
  6. Swap executes; attacker receives output tokens

Result: attacker, who is not on the allowlist, successfully swaps against
        the restricted pool, bypassing the intended access control.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
