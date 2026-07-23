### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, blocking allowlisted users from swapping through the official router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` — the router contract — not the originating user. When a pool admin allowlists specific user addresses, those users are silently blocked from swapping through `MetricOmmSimpleRouter` because the extension evaluates the router's address against the allowlist, not the user's address. The inverse is equally dangerous: if the router address is allowlisted, every user on the internet can bypass the curated-pool restriction entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes `msg.sender` of the pool's `swap` call: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end-user identity is never consulted.

This produces two symmetric failures:

**Failure A — valid user blocked (direct M-03 analog):** Pool admin allowlists `alice` and `bob` by address. Both users attempt to swap through the router. The extension sees the router address, which is not in the allowlist, and reverts with `NotAllowedToSwap`. Alice and bob must call the pool directly; the official periphery path is permanently broken for them.

**Failure B — allowlist bypass:** Pool admin allowlists the router address (a natural step to enable router-mediated swaps). The extension now passes for every caller regardless of identity, defeating the entire curation policy. Any unprivileged user can trade on a pool that was supposed to be restricted.

Note the contrast with `DepositAllowlistExtension`, which correctly checks the `owner` parameter (the address the LP position is minted to), not `sender`: [5](#0-4) 

The swap extension lacks the equivalent identity-preserving design.

### Impact Explanation

- **Failure A:** Allowlisted users on curated pools cannot use the official router. The primary user-facing swap path is broken for the exact population the allowlist was designed to serve. This is a broken core pool functionality causing an unusable swap flow.
- **Failure B:** Any unprivileged user can trade on a pool whose admin believed it was restricted. Unauthorized traders can drain liquidity at oracle prices or front-run allowlisted participants, causing direct loss of LP principal.

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract intended for deployment on curated pools. Any pool that configures it with per-address allowlisting (the primary use case) immediately exhibits Failure A for every router-mediated swap. Failure B is triggered the moment an admin allowlists the router address, which is a natural and documented configuration step. No privileged attacker capability is required; a normal user calling `exactInputSingle` is sufficient to trigger either failure.

### Recommendation

Replace the `sender` check with the originating user identity. Two options:

1. **Preferred:** Change `beforeSwap` to check `recipient` if the pool design guarantees recipient == user, or require the pool to pass the original `tx.origin`-equivalent through `extensionData`. More robustly, mirror the deposit extension pattern and have the router forward the real user address in `extensionData`, then decode it in the extension.

2. **Minimal fix:** Document that `sender` is the immediate pool caller and require pool admins to allowlist the router rather than individual users, then gate individual users inside the router itself — but this moves the security boundary off-chain and is not recommended.

The cleanest fix is to align `SwapAllowlistExtension` with `DepositAllowlistExtension`: gate on a user-controlled field that the router explicitly sets to the originating user, not on the implicit `msg.sender` chain.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Alice calls router.exactInputSingle({pool: pool, ...}).
4. Router calls pool.swap(recipient, ...) — msg.sender = router.
5. Pool calls _beforeSwap(router, ...).
6. Extension evaluates allowedSwapper[pool][router] == false → revert NotAllowedToSwap.
7. Alice's swap fails despite being explicitly allowlisted.

Bypass variant:
1. Pool admin calls setAllowedToSwap(pool, router, true).
2. Unprivileged user eve calls router.exactInputSingle({pool: pool, ...}).
3. Extension evaluates allowedSwapper[pool][router] == true → passes.
4. Eve trades on the curated pool with no individual authorization.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
