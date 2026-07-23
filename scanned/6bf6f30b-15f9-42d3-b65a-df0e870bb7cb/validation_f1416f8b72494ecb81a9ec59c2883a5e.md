### Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any caller to bypass the curated-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router, not the originating user. If the pool admin allowlists the router (the only way to make router-mediated swaps work for any user), every unprivileged address can bypass the curated-pool allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every hook:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the pool (`msg.sender` inside the extension is the pool):

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router itself the `msg.sender` of that call:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The router carries no mechanism to forward the originating user's address into the pool's `sender` slot. The pool has no alternative path to recover it.

**The dilemma this creates for pool admins:**

| Router allowlisted? | Effect |
|---|---|
| No | Router-mediated swaps revert for *every* user, including allowlisted ones |
| Yes | Every unprivileged address can bypass the allowlist by routing through the router |

There is no configuration that simultaneously supports router-mediated swaps and enforces the per-user allowlist.

---

### Impact Explanation

Any user who is not on the allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The pool admin's intent to restrict trading to specific counterparties is completely defeated. On pools where the allowlist is used to protect LP capital from adversarial flow (e.g., only trusted market makers are permitted), this allows arbitrary users to extract value from LPs at oracle-anchored prices, causing direct loss of LP principal. The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who want their LPs to be reachable through the standard router must allowlist it. The bypass requires no special privileges, no flash loans, and no multi-block setup — a single router call suffices. Any user who discovers the allowlist is enforced on direct calls but not on router calls can exploit it immediately.

---

### Recommendation

The allowlist must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into the `extensionData` slot for the allowlist extension; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` and fall back to a router-forwarded origin**: Add a dedicated field to the extension data that the router populates with the originating user, and have the extension prefer that field over the raw `sender` when the `sender` is a known router.

Either way, `allowedSwapper[pool][router]` must never be the sole gate for a curated pool.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can reach the pool via the router.
3. Pool admin calls setAllowedToSwap(pool, alice, true)
   — alice is the only intended trader.
4. Bob (not allowlisted) is blocked when he calls pool.swap() directly:
   allowedSwapper[pool][bob] == false → NotAllowedToSwap ✓

Attack
──────
5. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
6. Router calls pool.swap(...) — msg.sender inside pool.swap() == router.
7. Pool calls extension.beforeSwap(sender=router, ...).
8. Extension checks allowedSwapper[pool][router] == true → passes.
9. Bob's swap executes on the curated pool despite not being allowlisted. ✗
``` [5](#0-4) [6](#0-5) [1](#0-0)

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
