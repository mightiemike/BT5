### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. `MetricOmmPool.swap` always sets that argument to `msg.sender`, which is the **router contract** when a user routes through `MetricOmmSimpleRouter`. The extension therefore checks whether the router is allowlisted, not whether the actual end-user is allowlisted. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` uses that value to look up the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key for the pool dimension), and `sender` is the address the pool forwarded — the router, not the end-user.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

There are two broken outcomes:

1. **Router not allowlisted** — every router-mediated swap reverts with `NotAllowedToSwap`, even for users the admin explicitly allowlisted. Allowlisted users cannot use the standard periphery.
2. **Router allowlisted** (the only way to make the router work) — every user on the network can call `exactInputSingle` through the router and bypass the per-user gate entirely.

The same misbinding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to the intermediate hops inside `_exactOutputIterateCallback` where the router again calls `pool.swap` directly.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to a pool (e.g., KYC/AML-gated pools, institutional-only pools). When the allowlist is bypassed, any unpermissioned user can execute swaps against a pool that was configured to reject them. This breaks the core access-control invariant the extension is designed to enforce and allows unauthorized parties to drain or manipulate pool liquidity, constituting a direct loss of the pool's intended access boundary and potential loss of LP assets if the pool was designed to serve only vetted counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract. Any user who wants to swap will naturally use it. A pool admin who deploys a `SwapAllowlistExtension` and wants router-mediated swaps to work for their allowlisted users **must** allowlist the router address — there is no other option given the current architecture. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The pool must forward the original end-user identity to the extension, not the intermediary's address. Two complementary fixes:

1. **Router-side**: Store the original `msg.sender` in transient storage before calling the pool (analogous to how the payer is stored for the callback), and expose it via a standard interface so the pool can read it.
2. **Extension-side**: Accept an optional `originSender` field in `extensionData` and verify it against a signature or a trusted-router registry, then gate on that address instead of `sender`.

The simplest correct fix is for the router to encode the real user address in `extensionData` and for `SwapAllowlistExtension` to decode and check it when the caller is a known router, falling back to `sender` for direct pool calls.

---

### Proof of Concept

**Setup:**
- Pool P is deployed with `SwapAllowlistExtension` E configured in `BEFORE_SWAP_ORDER`.
- Pool admin calls `E.setAllowedToSwap(P, alice, true)` — only Alice is allowed.
- Pool admin calls `E.setAllowedToSwap(P, router, true)` — router is allowlisted so Alice can use it.

**Attack:**
1. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})`.
2. Router calls `P.swap(recipient, ...)` with `msg.sender = router`.
3. Pool calls `E.beforeSwap(router, recipient, ...)`.
4. Extension evaluates `allowedSwapper[P][router]` → `true`.
5. Bob's swap executes successfully despite not being on the allowlist.

**Root cause trace:** [1](#0-0) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 

`_beforeSwap` forwards that value unchanged to every configured extension. [3](#0-2) 

The extension checks `allowedSwapper[pool][sender]` — which is the router, not the end-user. [4](#0-3) 

The router calls `pool.swap` directly, making itself `msg.sender` to the pool with no mechanism to convey the original caller's identity.

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
