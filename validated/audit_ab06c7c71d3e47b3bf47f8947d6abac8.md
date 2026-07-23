### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender`, so the extension checks the router address — not the actual end user. If the pool admin allowlists the router (a natural configuration to let allowlisted users access the router), every user on-chain can bypass the allowlist by calling through the router.

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router] → true → passes
```

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with no forwarding of the original `msg.sender`: [4](#0-3) 

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → every user on-chain can bypass the allowlist by routing through the router.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, institutional market makers) and also allowlists the router so those users can access the standard periphery path inadvertently opens the pool to all users. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will see `sender = router`, which is allowlisted, and permit the swap. The admin-configured access boundary is fully bypassed through a supported public periphery path. This is an admin-boundary break: an unprivileged actor reaches a pool action the pool admin explicitly intended to gate.

### Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any pool admin who wants allowlisted users to be able to use the standard swap interface. The router is the primary user-facing entry point documented in the periphery. A pool admin who does not allowlist the router effectively makes the allowlist incompatible with the router, breaking the intended UX for legitimate users. The misconfiguration is therefore highly likely in any real deployment of a curated pool.

### Recommendation

The `beforeSwap` hook should receive the original end-user address, not the intermediary router address. Two approaches:

1. **Pass original caller through extension data**: The router should encode `msg.sender` into `extensionData` and the extension should decode and verify it, with the pool enforcing that the router is the `sender` when this path is used.
2. **Check `sender` only for direct pool calls**: The extension could treat the router as a trusted forwarder and require it to attest the real user identity in `extensionData`, rejecting router calls that do not carry a valid attestation.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap() with msg.sender = router
   → extension checks allowedSwapper[pool][router] == true → passes
   → Bob's swap executes in the restricted pool
5. The allowlist is fully bypassed.
``` [5](#0-4) [4](#0-3) [1](#0-0)

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
