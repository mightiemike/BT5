### Title
SwapAllowlistExtension gates the router's address instead of the end user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the end user. If the pool admin allowlists the router address to let their approved users access the router, every unpermissioned user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

**Concrete bypass scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC-approved addresses.
2. Admin allowlists Alice and Bob: `allowedSwapper[pool][alice] = true`, `allowedSwapper[pool][bob] = true`.
3. Admin also allowlists the public router so that Alice and Bob can use it: `allowedSwapper[pool][router] = true`.
4. Any unpermissioned user (Carol, Dave, …) calls `router.exactInputSingle(pool, ...)`. The pool sees `sender = router`, the extension finds `allowedSwapper[pool][router] = true`, and the swap proceeds — the allowlist is fully bypassed.

The router stores the original `msg.sender` only in transient storage for the payment callback; it is never forwarded to the pool or to extensions as the "real" swapper identity.

---

### Impact Explanation

The swap allowlist is the primary access-control mechanism for pools that restrict trading to approved counterparties (e.g., KYC/AML-gated pools, institutional-only venues). When bypassed, any address can execute swaps against the pool's LP reserves. Every swap extracts value from LPs at the oracle-anchored price; a non-allowlisted actor can drain LP principal by repeatedly swapping in the direction that empties the pool's bins. This is a direct loss of LP assets and breaks the core pool functionality the allowlist was deployed to protect.

---

### Likelihood Explanation

The trigger is a routine, well-motivated admin action: allowlisting the public router so that approved users can access the router's UX. Any pool admin who takes this step — which is the only way to let allowlisted users use the router — simultaneously opens the gate to all users. The router is a public, permissionless contract, so no special capability is required by the attacker. The bypass is reachable on every pool that has both `SwapAllowlistExtension` configured and the router allowlisted.

---

### Recommendation

The extension must check the **economically relevant actor**, not the intermediary. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present. This requires a convention between the router and the extension.

2. **Extension-side (simpler)**: Change `SwapAllowlistExtension.beforeSwap` to check `sender` only when `sender` is not a known router, and fall back to checking an address decoded from `extensionData` when the caller is a router. Alternatively, document clearly that the allowlist gates the direct caller of `pool.swap()` and that the router must **not** be allowlisted; instead, users must call the pool directly.

The cleanest long-term fix is for the router to forward the original caller's address in a standardised field of `extensionData` so that allowlist extensions can always check the true end user.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // approved user
  allowedSwapper[pool][router] = true   // admin adds router so alice can use it

Attack (Carol, not allowlisted):
  carol → router.exactInputSingle({pool: pool, ...})
       → pool.swap(msg.sender=router, ...)
       → _beforeSwap(sender=router, ...)
       → SwapAllowlistExtension.beforeSwap(sender=router)
            allowedSwapper[pool][router] == true  ✓  (no revert)
       → swap executes, carol extracts tokens from LP bins

Result:
  Carol bypasses the allowlist and drains LP funds.
  Alice's allowlist entry is irrelevant; the router entry is the effective gate.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
