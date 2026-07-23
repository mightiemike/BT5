Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the intermediary router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When any user routes through `MetricOmmSimpleRouter`, that `sender` is the router contract address, not the originating EOA. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every unprivileged user the ability to bypass the allowlist entirely by routing through the same public router.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the direct caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap(...)`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The `extensionData` field is passed through but `SwapAllowlistExtension` ignores it entirely — there is no in-band mechanism to recover the originating user identity. [6](#0-5) 

The existing unit tests only exercise direct pool calls (pool as `msg.sender` calling the extension), never router-mediated paths, so the bypass is untested: [7](#0-6) 

**Root cause:** The allowlist check is bound to the intermediary caller (router), not the originating user. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific participants (e.g., approved market makers, KYC'd addresses, or protocol-controlled accounts) is fully bypassable by any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost beyond normal gas. This breaks the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories. Consequences include unauthorized users trading on pools designed for curated participants, extracting value from LPs whose positions were priced assuming a controlled counterparty set, and compliance-gated pools losing their access control guarantees entirely.

## Likelihood Explanation

The bypass requires only a standard `exactInputSingle` (or any `exact*`) call through the public `MetricOmmSimpleRouter`. No special privileges, flash loans, or complex setup are needed. The required precondition — a pool configured with `SwapAllowlistExtension` and the router allowlisted — is the natural and expected operational state for any allowlisted pool that supports periphery routing. Likelihood is **Medium**: it requires the specific extension configuration, but this is the expected deployment pattern.

## Recommendation

The `SwapAllowlistExtension` must gate on the originating user, not the intermediary caller. Two viable approaches:

1. **Extension-data identity forwarding:** The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks that address. Requires coordinated changes to both the router and extension.
2. **Separate per-user router allowance:** Introduce a two-level check — gate the router at the pool level, and require the router itself to enforce a per-user allowlist before forwarding to the pool.

Using `tx.origin` is not recommended as it is incompatible with smart-contract wallets and multicall patterns.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true)    // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)   // required for router-mediated swaps
4. Non-allowlisted userB calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., ...})
5. Router calls pool.swap(...) with msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true.
7. userB's swap executes successfully — allowlist bypassed.
8. Direct call: userB calls pool.swap() directly →
       allowedSwapper[pool][userB] → false → NotAllowedToSwap (correctly blocked).
```

The bypass is exclusive to the router path. Direct pool calls are correctly gated, confirming the root cause is the wrong-actor binding in `SwapAllowlistExtension.beforeSwap`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
