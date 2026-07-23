### Title
`SwapAllowlistExtension` Bypass via `MetricOmmSimpleRouter` — Router Identity Replaces End-User Identity in Allowlist Check — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the router is allowlisted, every user — including those the pool admin explicitly excluded — can bypass the per-user swap restriction by routing through the public router.

---

### Finding Description

**Allowlist check (SwapAllowlistExtension)** [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender`.

**Pool wires `msg.sender` as `sender`** [2](#0-1) 

`_beforeSwap` receives `msg.sender` of the `swap()` call. When the user goes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end user.

**ExtensionCalling forwards it unchanged** [3](#0-2) 

The `sender` argument passed to every registered extension is exactly the pool's `msg.sender` — the router — with no mechanism to recover the original initiator.

**Contrast with DepositAllowlistExtension (correct)** [4](#0-3) 

The deposit extension ignores `sender` and checks `owner` — the actual position owner passed explicitly by the caller — so it correctly gates the economic actor even through the `MetricOmmPoolLiquidityAdder`. The swap extension has no equivalent "end-user" argument; it only receives `sender` (the router).

**Result — the admin cannot achieve selective per-user allowlisting for router-mediated swaps.** They face a binary choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user can swap through the router — allowlist is nullified |
| No | No user can swap through the router — router is unusable for this pool |

There is no configuration that allows "only allowlisted users may swap through the router."

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, institutional partners, or a closed beta) must allowlist the router for those users to access the pool via the standard periphery. Once the router is allowlisted, any unprivileged address can call `MetricOmmSimpleRouter` and execute swaps against the pool, bypassing the per-user gate entirely. LPs in such a pool are exposed to unrestricted swap flow from actors the pool was explicitly designed to exclude, enabling adverse-selection losses against LP principal.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery contract intended for real pool deployments. Any pool that (a) configures a swap allowlist for access control and (b) allowlists the router to support standard periphery usage is immediately vulnerable. Both conditions are the natural, expected configuration for a restricted pool that still wants to support the standard router UX. The bypass requires no special privilege — any EOA or contract can call `MetricOmmSimpleRouter`.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic initiator**, not the intermediary. Two viable approaches:

1. **Pass initiator through `extensionData`**: Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address. This requires a convention between router and extension.

2. **Check `sender` AND require direct calls**: Document that the allowlist only works for direct pool calls (no router), and have the router expose a separate entry point that pools can gate differently.

The deposit extension's pattern — checking `owner` rather than `sender` — is the correct model. The swap extension needs an equivalent "end-user" identity that survives router intermediation.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, userA, true)       // only userA is allowed
3. Admin calls setAllowedToSwap(pool, router, true)      // router must be allowlisted
                                                          // for userA to use it
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, ...)
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
5. userB's swap executes successfully against the LP pool,
   despite never being added to the allowlist.
```

The check that was supposed to block `userB` is evaluated against the router's identity, not `userB`'s — an exact structural analog of the external report's "guard applied to the pre-modification value, not the post-modification value": here the guard is applied to the intermediary identity, not the end-user identity, so the intended protection is bypassed.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
