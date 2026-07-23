### Title
`SwapAllowlistExtension` gates on the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` (i.e., whoever called `pool.swap`). When a swap is routed through `MetricOmmSimpleRouter`, that caller is the router contract, not the original user. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently grants every user on-chain the ability to bypass the allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when the router calls swap
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that forwarded `sender` value.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When the router is the caller, `sender` = `address(router)`, so the lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Step 3 — The router never forwards the original user's address to the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly; the original `msg.sender` is stored only in transient callback context (for payment), never passed as a swap argument:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 4 — The irreconcilable conflict.**

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces two mutually exclusive choices:

| Admin action | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also pass — bypass achieved** |

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

If the pool admin allowlists the router (a natural action to support router-mediated swaps for their curated participants), any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and bypass the `SwapAllowlistExtension` entirely. The unauthorized user receives oracle-anchored output tokens from the pool's LP reserves, directly reducing LP asset value and violating the curation guarantee the pool was configured to enforce.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. However, this is the natural and expected action for any pool admin who wants their allowlisted users to access multi-hop routing or the standard periphery UX. The admin has no way to achieve "allowlisted users can use the router, others cannot" — so any admin who tries to support router access will inadvertently open the pool to all users.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `sender` only for direct pool calls; require a signed or trusted forwarding mechanism for router calls**: The extension inspects whether `sender` is a known router and, if so, decodes the real user from `extensionData`.

The simplest short-term fix is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must not be used on pools that allow router access, until a forwarding mechanism is implemented.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists Alice: allowedSwapper[pool][alice] = true
  - Pool admin allowlists the router so Alice can use it: allowedSwapper[pool][router] = true

Attack:
  - Eve (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Eve's swap executes successfully, bypassing the allowlist
``` [5](#0-4) [4](#0-3) [1](#0-0)

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
