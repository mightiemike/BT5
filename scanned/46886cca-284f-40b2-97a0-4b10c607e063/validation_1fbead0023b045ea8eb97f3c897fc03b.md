### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is always `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router to enable standard periphery access, every unpermissioned user bypasses the curated-pool guard entirely.

---

### Finding Description

`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
  msg.sender,          // ← always the direct caller; equals router when routed
  recipient,
  zeroForOne,
  amountSpecified,
  priceLimitX64,
  packedSlot0Initial,
  bidPriceX64,
  askPriceX64,
  extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol lines 160-177
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, ...)
  )
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap` directly without injecting the real user's address:

```solidity
// MetricOmmSimpleRouter.sol lines 71-80
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
``` [4](#0-3) 

The real user's address is stored only in transient storage for the payment callback; it is never surfaced to the pool or to any extension. Consequently the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This produces two irreconcilable failure modes for any pool that deploys `SwapAllowlistExtension`:

| Admin configuration | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| Allowlist individual users only | **Reverts** (router not listed) | Reverts |
| Allowlist the router | Passes | **Passes — bypass** |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

The analogous structural flaw to the seed report: just as the deleted short record's `tokenId` was never cleared so the old owner retained authority over the new owner's NFT, here the pool's `sender` binding is never updated to reflect the real user, so the router's identity permanently substitutes for the actual swapper identity in every guard decision.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then adds `allowedSwapper[pool][router] = true` — the natural step to let their allowlisted users reach the pool through the standard periphery — simultaneously grants every unpermissioned address the ability to swap. Any actor can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the guard passes because the router is allowlisted. The curated-pool invariant is broken: non-KYC'd, non-whitelisted, or otherwise excluded counterparties can trade against the pool's LPs, causing direct LP-principal loss through adverse selection and removing the protection the extension was deployed to provide.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's primary user-facing swap interface. A pool admin who wants their allowlisted users to access the pool through the standard UI will naturally add the router to the allowlist. Nothing in the `SwapAllowlistExtension` interface, its setter functions, or the router warns that doing so voids per-user gating. The mistake is a single `setAllowedToSwap(pool, router, true)` call away from any pool admin who wants router compatibility.

---

### Recommendation

The `sender` identity passed to `beforeSwap` must reflect the economic actor, not the intermediary contract. Two viable approaches:

1. **Extension-data forwarding**: Require the router to ABI-encode the real `msg.sender` into `extensionData` for allowlist-protected pools, and have `SwapAllowlistExtension` decode and check that value when present. The pool already forwards `extensionData` unchanged to every hook.

2. **Trusted-forwarder pattern**: Add an optional `trustedSender` field to the pool's `swap` signature (or a separate entry-point) that the router populates with `msg.sender`, and have `_beforeSwap` pass that value as `sender` when the caller is a factory-registered router.

Until one of these is implemented, the `SwapAllowlistExtension` documentation must explicitly state that allowlisting the router disables per-user gating and that per-user enforcement requires direct pool calls only.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)      // allowlist Alice
3. Admin calls setAllowedToSwap(pool, router, true)     // enable router for Alice

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          <curated pool>,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           extensionData: ""
       })

5. Router calls pool.swap(bob, true, X, limit, "", "")
   → pool sees msg.sender = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
   → swap executes; Bob receives output tokens

Result: Bob, who is not on the allowlist, successfully swaps in the curated pool.
        The SwapAllowlistExtension guard is completely bypassed.
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
