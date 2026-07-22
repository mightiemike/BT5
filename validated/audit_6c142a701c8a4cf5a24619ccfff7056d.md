### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating user. If the pool admin allowlists the router so that legitimate users can trade through it, the check becomes `allowedSwapper[pool][router] == true`, which passes for every caller regardless of their individual allowlist status. Any unprivileged user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

At this point `msg.sender` inside `MetricOmmPool.swap` is the **router address**, so `sender` forwarded to the extension is the router, not the originating user. The extension evaluates `allowedSwapper[pool][router]` — a single entry that covers every user who routes through the router.

**The dilemma this creates for pool admins:**

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Individually allowlisted users cannot use the router at all; they must call the pool directly |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by routing through the router |

The second branch is the exploitable path: a pool admin who wants their allowlisted users to be able to use the standard periphery router will allowlist the router address, inadvertently opening the pool to all callers.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's LP liquidity, extracting value at oracle-anchored prices that the pool's LPs did not consent to provide to arbitrary counterparties. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

Likelihood is **high**. `MetricOmmSimpleRouter` is the canonical, production-grade swap entry point documented in the periphery. Pool admins who configure `SwapAllowlistExtension` and want their allowlisted users to have a normal UX will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must check the **originating user**, not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** The router already stores the original `msg.sender` as the payer in transient storage. Extend the `extensionData` or a dedicated field so the pool/extension can recover the true originator. This requires a protocol-level convention.

2. **Check `recipient` or require direct-pool-only access.** For the simpler case, gate on `recipient` (the address receiving tokens) rather than `sender`, since the router always sets `recipient` to the user-supplied address. This is not a perfect fix (recipient can be a third party) but is closer to the economic actor.

3. **Preferred: allowlist the router separately and require the router to forward user identity.** Modify `MetricOmmSimpleRouter` to encode the originating `msg.sender` into `extensionData` and modify `SwapAllowlistExtension` to decode and check that identity when the immediate `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls curated_pool.swap(bob, true, X, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the curated pool's LP liquidity
     despite never being individually allowlisted.

Result: bob bypasses the curated allowlist and trades against LP funds
        that were only meant to be accessible to alice and other approved counterparties.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
