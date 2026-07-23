Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` resolves to the router contract address rather than the originating user. Any pool admin who allowlists the router to support legitimate router-mediated swaps simultaneously grants unrestricted swap access to every user on the network, because the extension cannot distinguish between different callers behind the same router.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract: [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. There is no configuration that simultaneously allows legitimate users to route through `MetricOmmSimpleRouter` and blocks non-allowlisted users from doing the same. If the router is allowlisted, every user on the network can bypass the allowlist by routing through it. If the router is not allowlisted, all router-mediated swaps revert, including legitimate ones.

`DepositAllowlistExtension` does not share this flaw because it gates `owner` — an explicit parameter the liquidity adder can set to the actual user rather than the intermediary: [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd institutions, protocol-owned addresses, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. A non-allowlisted user can execute swaps at live oracle prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes a direct loss of LP assets through unauthorized price-taking against restricted liquidity — a broken core pool functionality causing loss of funds for LPs.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and supported by the protocol. Any pool admin who wants legitimate users to access the pool via the router must allowlist the router address. The bypass is reachable on every curated pool that supports router-mediated swaps, triggered by a single unprivileged call to `exactInputSingle` or `exactInput` by any arbitrary user. No special privileges or setup beyond a standard router call are required.

## Recommendation
Pass the originating user through the swap path so the allowlist can gate the economically relevant actor:

1. **Transient storage approach**: Have `MetricOmmSimpleRouter` write the originating `msg.sender` into a transient storage slot before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` read that slot (via a known interface) when `sender` is a recognized router address.

2. **Interface extension**: Add a `payer`/`originator` field to the swap interface that the router populates with `msg.sender` before calling the pool, and forward it as a distinct argument to `beforeSwap` so extensions can check it independently of `sender`.

The simplest safe fix is option 1: the router sets a transient storage slot with the originating caller before the pool call, and the extension reads it when `sender` matches a known router address.

## Proof of Concept
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

Foundry test outline:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter
  - Pool admin calls setAllowedToSwap(pool, router, true)
  - Confirm attacker address is NOT in allowedSwapper
  - Call router.exactInputSingle from attacker address
  - Assert swap succeeds (no NotAllowedToSwap revert)
  - Confirm attacker received output tokens
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
