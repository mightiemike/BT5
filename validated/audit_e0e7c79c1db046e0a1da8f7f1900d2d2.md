### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the originating user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

**`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value above:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool:** [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Consequence:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants to allow legitimate users to swap via the router must allowlist the router address. Once the router is allowlisted, the check becomes `allowedSwapper[pool][router] == true` for every caller, regardless of who the originating user is. The allowlist is completely defeated for any user who routes through the router.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC-verified counterparties, institutional market makers, or whitelisted protocols) loses that restriction entirely for any user who calls the pool through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps, drain liquidity at oracle-anchored prices, and expose LPs to adverse selection or toxic flow that the allowlist was specifically designed to prevent. This is a direct broken-invariant on the pool's core access-control functionality.

---

### Likelihood Explanation

- The router is the canonical, documented periphery entry point for swaps.
- Any pool admin who wants legitimate allowlisted users to be able to use the router (the normal UX path) must allowlist the router address.
- Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no malicious setup, and no non-standard tokens.
- The attacker only needs to call `exactInputSingle` or any other router entry point targeting the curated pool.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two sound approaches:

1. **Pass `tx.origin` as an additional argument** — rejected on principle (phishing risk, contract-wallet incompatibility).

2. **Require the router to forward the originating user identity** — the router should accept an `originatingUser` parameter and pass it in `extensionData`; the extension decodes it and checks `allowedSwapper[pool][originatingUser]`. The extension must also verify that `msg.sender` (the pool) is a registered pool so the data cannot be spoofed by a direct pool call with crafted `extensionData`.

3. **Gate on `sender` but never allowlist the router** — document that the router must never be added to the allowlist; allowlisted users must call the pool directly. This is operationally fragile and breaks the UX for curated pools.

Option 2 is the most robust. The extension interface already carries `extensionData` through every hook, making this feasible without changing the core pool.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][alice]  = true   // legitimate user
  allowedSwapper[pool][router] = true   // admin adds router so alice can use it
  allowedSwapper[pool][bob]    = false  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
    pool:      pool,
    recipient: bob,
    zeroForOne: true,
    amountIn:  X,
    ...
  })

  → router calls pool.swap(bob_recipient, true, X, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives output tokens

Result: bob, who is explicitly NOT on the allowlist, successfully swaps on the curated pool.
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
