### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. If the pool admin allowlists the router (a natural action to let their approved users access the router), every user — including non-allowlisted ones — can bypass the gate entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Consequence**: The allowlist lookup becomes `allowedSwapper[pool][router]`. The extension has no access to the originating user's address. The admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **Every** user can bypass the allowlist via the router |

There is no option to "allow router-mediated swaps only for allowlisted users." Allowlisting the router is a natural, expected admin action (it is the only way to let approved users access the router), yet it silently opens the pool to all callers.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, regulated participants, or protocol-internal actors). Once the admin allowlists the router to enable better UX for their approved users, the restriction is completely nullified: any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps in the restricted pool. Non-approved users gain full swap access, breaking the core access-control invariant the extension is designed to enforce.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a **reasonable and expected** administrative action — without it, none of the approved users can use the router. The admin has no indication from the code or interface that doing so opens the pool to all users. The condition is therefore likely to occur in any production deployment where the pool admin wants router support for their approved users.

---

### Recommendation

The extension must verify the **originating user**, not the intermediary caller. Two viable approaches:

1. **Pass the payer/originator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router identity check (e.g., verify `msg.sender` of the extension call is a known factory pool, then trust the encoded originator only when the outer caller is the router).

2. **Check `sender` and reject known router addresses unless the originator is also allowlisted**: Require the router to forward the originating user address in a verifiable way (e.g., a signed payload or a transient-storage slot written by the router before calling the pool).

The simplest safe default: document that allowlisting the router grants access to **all** router users, and provide a separate `RouterSwapAllowlistExtension` that decodes the originator from `extensionData` and verifies it against the allowlist.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (FACTORY = F)
  - Admin allowlists alice (approved user) via setAllowedToSwap(pool, alice, true)
  - Admin allowlists router via setAllowedToSwap(pool, router, true)
    (so alice can use the router)

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  ✓
  - Swap executes for bob — allowlist bypassed

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [6](#0-5) [7](#0-6)

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
