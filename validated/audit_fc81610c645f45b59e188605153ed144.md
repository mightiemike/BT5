### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling full allowlist bypass via the periphery router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` on the pool — the router contract when a user enters through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the gate to every user, because the extension cannot distinguish which end-user the router is acting for.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

The pool passes its own `msg.sender` as `sender`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` on the pool is the router, and `sender` forwarded to the extension is the router address — not the end-user.

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two broken states result:**

1. **Allowlist blocks legitimate users.** If the admin allowlists individual KYC'd users but not the router, those users cannot swap through the router even though they are explicitly permitted.

2. **Allowlist bypass.** If the admin allowlists the router address (the natural step to enable router-mediated swaps for permitted users), every user — including those not on the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner, which is always the intended economic actor regardless of who pays), but `SwapAllowlistExtension` has no equivalent indirection.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., for regulatory compliance, to prevent toxic arbitrage flow, or to gate a private market) loses that protection entirely once the router is allowlisted. Any unprivileged user can execute swaps on the curated pool by routing through `MetricOmmSimpleRouter`, draining LP value through arbitrage or violating the pool's access policy. This is an admin-boundary break: the pool admin's restriction is bypassed by an unprivileged path.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router. This is the natural operational step any admin would take when deploying a curated pool that is expected to be used through the standard periphery. The admin has no indication from the extension's interface or documentation that allowlisting the router opens the gate to all users. Likelihood is medium-high for any production curated pool that supports router access.

---

### Recommendation

The extension must identify the actual end-user, not the immediate caller. Two approaches:

1. **Pass the real user through `extensionData`.** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender` for swap allowlisting.** The recipient is the address that receives output tokens and is the economically relevant actor. The pool already passes `recipient` as the second argument to `beforeSwap`.

3. **Mirror the deposit extension pattern.** Gate by a caller-supplied "owner" field rather than the immediate `msg.sender` on the pool.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists user A:  allowedSwapper[P][A] = true
  admin allowlists router R: allowedSwapper[P][R] = true
    (natural step to let A use the router)

Attack:
  user B (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Router calls:
    P.swap(recipient, ...)   // msg.sender on pool = router R

  Pool calls:
    E.beforeSwap(sender=R, ...)

  Extension checks:
    allowedSwapper[P][R] == true  →  passes

  Result: B's swap executes on the curated pool, bypassing the allowlist.
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
