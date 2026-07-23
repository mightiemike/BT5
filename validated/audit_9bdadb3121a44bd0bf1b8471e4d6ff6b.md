### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unpermissioned user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument in the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` at that point is the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` — a single entry that covers every user who routes through the same public contract. If the pool admin has added the router to the allowlist (the only way to let any legitimate user trade via the router), the gate is permanently open to all callers.

The same substitution occurs in `exactInput` multi-hop: [5](#0-4) 

and in the recursive `exactOutput` callback path: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a curated venue — only explicitly approved addresses may trade. Once the pool admin allowlists the router (necessary for any legitimate user to trade via the supported periphery), the allowlist is effectively nullified: any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the router, not the caller. Unauthorized users can drain LP value at oracle-derived prices on pools that were designed to be restricted, constituting a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The trigger requires only a standard call to the public `MetricOmmSimpleRouter` — no special privileges, no flash loans, no unusual tokens. The precondition (router allowlisted) is the normal operational state for any pool that intends to support router-mediated swaps. Any user who discovers the pool address and the router address can exploit this immediately.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the call chain.** The router should forward the end-user address in `extensionData` (or a dedicated field), and the extension should decode and check that address instead of `sender`.

2. **Alternatively, check `recipient` instead of `sender` for router flows**, or require the pool to expose a trusted-forwarder mechanism so the extension can recover the original caller.

A minimal immediate fix in `SwapAllowlistExtension`:

```solidity
// Instead of checking sender (which is the router when routed),
// decode the real user from extensionData if present, else fall back to sender.
function beforeSwap(
    address sender,
    address,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata extensionData
) external view override returns (bytes4) {
    address actor = extensionData.length >= 20
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][actor]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router must then encode `msg.sender` into `extensionData` before forwarding to the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // legitimate user
  allowedSwapper[pool][router] = true   // required for alice to use the router
  allowedSwapper[pool][bob]    = false  // bob is NOT allowed

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      <curated pool>,
      recipient: bob,
      ...
  })

  pool.swap(msg.sender=router, ...)
  → _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] == true  ✓  (passes)

Result:
  bob's swap executes on the curated pool despite not being allowlisted.
  The allowlist invariant is broken for every pool that supports router access.
``` [7](#0-6) [8](#0-7) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
