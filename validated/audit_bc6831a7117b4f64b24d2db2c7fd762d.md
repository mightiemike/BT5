### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (which is required for any router-based swap to succeed on an allowlisted pool), every unpermissioned user can bypass the swap allowlist by routing through the router.

---

### Finding Description

**Call chain for a direct swap (correct):**
```
user → pool.swap()
  pool: _beforeSwap(msg.sender = user, ...)
  SwapAllowlistExtension.beforeSwap(sender = user, ...)
  → checks allowedSwapper[pool][user]  ✓
```

**Call chain through the router (broken):**
```
user → MetricOmmSimpleRouter.exactInputSingle(params)
  router → pool.swap(params.recipient, ...)
    pool: _beforeSwap(msg.sender = router, ...)
    SwapAllowlistExtension.beforeSwap(sender = router, ...)
    → checks allowedSwapper[pool][router]  ✗
```

The pool always passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

**The dilemma this creates for the pool admin:**

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlists the router** | Every user on the network can bypass the allowlist |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or institutional LPs). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool. The pool's access-control invariant is fully broken for all router-mediated paths, which is the standard user-facing swap interface. LPs who deposited into a restricted pool under the assumption that only trusted counterparties could trade against their liquidity are exposed to unrestricted public swap flow.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical, publicly deployed swap interface for the protocol.
- Any user who discovers the allowlist restriction can trivially route through the router instead of calling the pool directly.
- No privileged access, special tokens, or malicious setup is required — a standard `exactInputSingle` call suffices.
- The pool admin enabling router-based swaps for their allowlisted users is the expected operational pattern, making the router-allowlist entry the natural configuration path.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end user's identity**, not the intermediary's. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the router's `extensionData` payload and have the extension decode and check it. This requires the router to sign or attest the real caller, which is complex and trust-dependent.

2. **Check `recipient` instead of `sender` in the extension.** The `recipient` is the address that receives swap output and is set by the end user, not the router. However, `recipient` can be set to any address, so this is not a reliable identity check either.

3. **Preferred: gate on `sender` but require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly. Enforce this with a factory-level check or NatSpec warning.

4. **Cleanest fix:** Extend the `beforeSwap` hook signature or `extensionData` convention so the router forwards the original `msg.sender` in a verifiable way (e.g., a signed attestation or a trusted forwarder pattern), and have the extension verify that field.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedUser` is allowlisted.
// Admin also allowlists the router so trustedUser can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), trustedUser, true);

// Attack: attacker (not allowlisted) routes through the router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: attacker,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ Swap succeeds — allowlist bypassed.
// The extension checked allowedSwapper[pool][router] = true, not allowedSwapper[pool][attacker].
```

The `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` (true) rather than `allowedSwapper[pool][attacker]` (false), so the guard passes and the attacker swaps on a pool they were never permitted to access. [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
