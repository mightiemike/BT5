### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the **actual user**. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted address can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual user's. The same misbinding occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

This creates an irreconcilable conflict for the pool admin:

- **Router not allowlisted**: allowlisted users cannot use the router at all; their router-mediated swaps revert with `NotAllowedToSwap`.
- **Router allowlisted**: every non-allowlisted address can bypass the per-user gate by routing through the router, because the extension sees the allowlisted router address and passes the check unconditionally.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool's liquidity without being on the allowlist, directly violating the pool's access-control invariant. Because the router is a public, permissionless contract, the bypass requires no special privilege.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any user aware of the router can trivially exploit this. The pool admin has no on-chain mechanism to simultaneously allowlist the router (for legitimate users) and block non-allowlisted users from using it. The bypass is therefore reachable by any unprivileged actor as soon as the router is allowlisted.

---

### Recommendation

The extension must check the **originating user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `sender` (the address the pool received as `msg.sender`) only when the caller is a known trusted router; otherwise require the pool to pass the true end-user. A cleaner approach is to have the router forward the originating user explicitly in `extensionData` and have the extension decode it, or to have the pool expose a separate `swapOnBehalf(address user, ...)` entry point that records the true user.

2. **Preferred fix**: change `SwapAllowlistExtension.beforeSwap` to check the `sender` argument against the allowlist **and** require that `sender == msg.sender` (i.e., no intermediary), or introduce a trusted-forwarder pattern where the router attests the real user in `extensionData` and the extension verifies the attestation.

Minimal patch sketch:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    // sender is msg.sender of pool.swap(); for router hops this is the router, not the user.
    // Gate on sender only when sender == tx.origin, or require extensionData to carry the real user.
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The root fix is ensuring `sender` always represents the economically relevant actor, not an intermediary router.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  alice = allowlisted: setAllowedToSwap(pool, alice, true)
  bob   = NOT allowlisted
  router = MetricOmmSimpleRouter (public, permissionless)

Step 1 – Pool admin allowlists the router so alice can use it:
  setAllowedToSwap(pool, address(router), true)

Step 2 – bob (not allowlisted) calls:
  router.exactInputSingle({pool: pool, recipient: bob, ...})

Step 3 – Router calls:
  pool.swap(bob_recipient, zeroForOne, amount, limit, "", extensionData)
  // msg.sender to pool = router

Step 4 – Pool calls extension:
  extension.beforeSwap(sender=router, ...)
  // checks allowedSwapper[pool][router] → true  ✓ (passes!)

Step 5 – Swap executes; bob receives output tokens.

Result: bob, a non-allowlisted address, successfully swaps in a pool
        that was intended to be restricted to alice only.
```

The allowlist invariant is broken: the extension checks the router's identity instead of the actual swapper's identity, directly analogous to the LlamaCore bug where any role could cast an approval because the role-to-approvalRole binding was never verified.

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
