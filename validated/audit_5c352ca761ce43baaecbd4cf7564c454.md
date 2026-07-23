The code confirms the claim. Let me trace through each component:

**1. `SwapAllowlistExtension.beforeSwap` checks `sender` (the pool's direct caller):** [1](#0-0) 

**2. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`:** [2](#0-1) 

**3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — the router is `msg.sender` at the pool level; the real user's address is stored only in transient storage for the payment callback, never forwarded to the pool:** [3](#0-2) 

**4. `ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension — no real-user identity is injected:** [4](#0-3) 

The router stores the real user only via `_setNextCallbackContext` for payment purposes, not for identity attestation to the extension. The extension receives the router address as `sender` and has no access to the actual end-user. The vulnerability is confirmed.

---

Audit Report

## Title
`SwapAllowlistExtension` allowlist bypassed via router: router address checked instead of end-user — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every address the ability to bypass the per-user allowlist, allowing non-allowlisted users to trade against LP funds in a curated pool.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:
```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which originates from `MetricOmmPool.swap` as `msg.sender` of the pool's own `swap` call:
```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
```
When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any router swap function), the router calls `pool.swap(...)` directly. The pool's `msg.sender` is the router. The router stores the real user's address only in transient storage for the payment callback (`_setNextCallbackContext`), and never forwards it to the pool or extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. Once the router is allowlisted, the check passes for any caller of the router.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` is designed to restrict which counterparties can trade against LP funds. Once the router is allowlisted (the only way to permit any router-mediated swap), the allowlist is nullified for all users. Non-allowlisted users can drain LP-provided liquidity at oracle prices, causing direct loss of LP principal. This matches the "High direct loss or curation failure if disallowed users can still trade or deposit" impact gate.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard periphery for swaps. Any pool admin deploying a curated pool with `SwapAllowlistExtension` who also wants users to use the router (the normal UX path) will allowlist the router. The bypass requires no special privilege — any address can call the router. The attacker needs only the router address and pool address, both of which are public.

## Recommendation
The extension must resolve the actual end-user identity, not the intermediary. Options:
1. **Pass the real user through the router via `extensionData`:** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and checks it. This requires the router to be trusted to not forge identities.
2. **Check `recipient` instead of `sender`:** If the pool's design intent is that the *recipient* of swap proceeds is the gated party, check `recipient` (the second argument to `beforeSwap`). This holds for direct swaps but may not hold for all router configurations.
3. **Reject router-mediated swaps entirely:** Require `sender == tx.origin` or maintain a registry of trusted forwarders that attest to the real caller identity.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  2. Router calls pool.swap(recipient=attacker, ...)
     [pool.msg.sender = router]
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap
  5. Extension checks allowedSwapper[pool][router] → true → passes
  6. Swap executes; attacker receives tokens from LP funds.

Result:
  - Attacker bypassed the per-user allowlist.
  - LP funds transferred to non-allowlisted counterparty.
  - allowedSwapper[pool][attacker] was never set to true.
```

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
