### Title
`SwapAllowlistExtension` checks the router's address instead of the actual end-user when swaps are routed through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract**, not the originating user. If the pool admin allowlists the router so that legitimate users can trade through it, every non-allowlisted user can bypass the guard by routing through the same public router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, `msg.sender` is captured and forwarded as `sender` to every before-swap hook:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this verbatim and dispatches it to the extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for pool-identity namespacing), and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, which is the router's allowlist status, not the originating user's.

**Two broken states result:**

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert, even for legitimately allowlisted users — broken UX |
| Router **allowlisted** | Every user, regardless of allowlist status, can bypass the guard by routing through the public router |

The second state is the critical one: a pool admin who allowlists the router to support normal UX inadvertently opens the guard to the entire public.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` (e.g., to restrict trading to KYC'd counterparties, institutional LPs, or whitelisted market makers) loses its access control entirely. Any non-allowlisted address can execute swaps against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This constitutes a direct policy bypass with fund-impacting consequences: unauthorized parties can drain LP value through arbitrage or directional trading that the pool admin explicitly intended to block.

---

### Likelihood Explanation

The bypass requires only a single public call to the canonical periphery router — no special privileges, no flash loans, no multi-step setup. Any user who discovers the pool is allowlist-gated can immediately attempt the router path. The router is a documented, supported entrypoint, so the bypass is trivially reachable.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediate caller. Two complementary fixes:

1. **Pass the original payer/initiator through the router.** The router already stores `msg.sender` in transient storage as the payer (`_setNextCallbackContext(..., msg.sender, ...)`). The pool could expose this via a dedicated `initiator` argument in the swap hook, or the router could pass the real user as `recipient` context.

2. **Check `recipient` or a dedicated `initiator` field in `beforeSwap`.** The extension signature already receives `recipient`; if the pool contract is redesigned to pass the true originator as a separate argument, the extension can gate on that.

3. **Short-term mitigation:** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and must require all allowlisted users to call the pool directly. This breaks router UX but preserves the guard.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists address(router) so legitimate users can trade via router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted)
  5. Swap executes — attacker trades on the curated pool without being allowlisted

Result: attacker bypasses the swap allowlist and executes an unauthorized swap.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
