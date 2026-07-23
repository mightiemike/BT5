### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating user. A pool admin who allowlists the router to permit legitimate router-mediated swaps simultaneously opens the allowlist to every user on the network, because the extension cannot distinguish between different callers behind the same router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so `sender = router`: [3](#0-2) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**The dilemma this creates for the pool admin:**

| Router allowlisted? | Legitimate user via router | Attacker via router |
|---|---|---|
| No | REVERTS (legitimate users cannot use the router) | REVERTS |
| Yes | PASSES | PASSES (bypass) |

There is no configuration that simultaneously allows legitimate users to route through `MetricOmmSimpleRouter` and blocks non-allowlisted users from doing the same. Any pool that allowlists the router address effectively disables its own swap allowlist for all router-mediated paths.

The `DepositAllowlistExtension` does not share this flaw because it gates `owner` (the position beneficiary), which is an explicit parameter the liquidity adder can set to the actual user rather than itself. [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd institutions, protocol-owned addresses, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user can execute swaps at live oracle prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes a direct loss of LP assets through unauthorized price-taking against restricted liquidity.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and supported by the protocol. Any pool admin who wants legitimate users to access the pool via the router must allowlist the router. The bypass is therefore reachable on every curated pool that supports router-mediated swaps, triggered by a single unprivileged call to `exactInputSingle` or `exactInput`.

---

### Recommendation

Pass the originating user through the swap path so the allowlist can gate the economically relevant actor. Two options:

1. **Add a `payer` / `originator` field to the swap interface** that the router populates with `msg.sender` before calling the pool, and forward it as a distinct argument to `beforeSwap` so extensions can check it independently of `sender`.

2. **Check `sender` in the router context**: require the router to pass the original caller as the `recipient`-equivalent identity, or have the extension read the payer from transient storage set by the router before the pool call.

The simplest safe fix is to have `MetricOmmSimpleRouter` write the originating `msg.sender` into a transient storage slot before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` read that slot (via a known interface) when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin allowlists router for legitimate users)
  - allowedSwapper[pool][attacker] = false (attacker is NOT allowlisted)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
  2. Router calls pool.swap(recipient=attacker, ...)
  3. pool.msg.sender = router → _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES
  5. Attacker executes swap against restricted LP capital at live oracle price

Result:
  - Attacker bypasses the swap allowlist entirely
  - LP funds are traded against by an unauthorized counterparty
  - Pool admin's curation policy is silently voided
``` [5](#0-4) [6](#0-5) [2](#0-1)

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
