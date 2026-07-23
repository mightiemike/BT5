### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the gate to every user on-chain, defeating the entire allowlist.

---

### Finding Description

**Hook binding in the pool**

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value verbatim as the first argument to every configured extension: [2](#0-1) 

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap` reads `sender` (the first parameter) and checks it against the per-pool allowlist: [3](#0-2) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][real_user]`. The real user's identity is never forwarded to the extension.

**The broken invariant**

The pool admin configures the allowlist to restrict which *users* may trade. To make the router usable for those users, the admin must add the router to `allowedSwapper`. The moment the router is allowlisted, the check degenerates to "is the router allowed?" — which is always true — and every user on-chain can trade through the router regardless of their individual allowlist status.

Contrast with `DepositAllowlistExtension`, which correctly checks `owner` (the position owner forwarded explicitly by the pool), not `msg.sender` of the pool: [5](#0-4) 

The swap path has no equivalent "owner" forwarding; the only identity the pool exposes to the extension is its own `msg.sender`.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) is fully bypassed by any user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool receives and settles the swap normally; the extension never sees the real user. LP assets are exposed to unrestricted trading that the pool admin explicitly intended to prevent, constituting a direct loss of curation control and potential fund-impacting unauthorized execution.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role or setup is required. The only precondition is that the pool admin allowlists the router (a natural operational step to make the router usable for legitimate allowlisted users). Once that step is taken, the bypass is unconditional and requires no further attacker capability.

---

### Recommendation

The pool must forward the originating user's address to the extension in a way the extension can verify. Two sound approaches:

1. **Dedicated `originator` parameter**: Add an `originator` field to the `beforeSwap` hook signature (alongside `sender`). The pool sets `originator = msg.sender` for direct calls and the router sets it via `extensionData` with a verifiable signature or transient-storage context. The extension checks `originator`.

2. **Extension-data convention**: Define a standard encoding where the router prepends the real user's address to `extensionData`. The extension decodes and checks that address, and the pool's `onlyPool` guard ensures only a legitimate pool can invoke the hook. This mirrors how the liquidity adder already uses `extensionData` for callback kind signaling.

Until fixed, pool admins should not allowlist the router on pools that rely on `SwapAllowlistExtension` for per-user access control.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; pool admin calls:
       setAllowedToSwap(pool, router, true)   // router allowlisted so users can route
       setAllowedToSwap(pool, alice, true)    // alice is the only intended user
       // bob is NOT allowlisted

2. Bob (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

3. Router calls pool.swap(recipient, ...) — msg.sender = router

4. Pool calls _beforeSwap(sender=router, ...)

5. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes

6. Bob's swap executes successfully despite not being on the allowlist.

Expected: revert NotAllowedToSwap()
Actual:   swap settles, bob receives output tokens
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
