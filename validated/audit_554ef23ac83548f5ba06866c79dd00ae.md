### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the immediate `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users use the standard periphery), every non-allowlisted user can bypass the guard by routing through the same router.

---

### Finding Description

`ExtensionCalling._beforeSwap` encodes and forwards `sender` — the value the pool received as `msg.sender` — to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making `msg.sender` of the pool call equal to the router, not the originating user: [3](#0-2) 

The actual user's address is stored only in the router's transient callback context for payment settlement; it is never forwarded to the pool as `sender`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma for any pool that deploys `SwapAllowlistExtension`:

| Pool admin action | Consequence |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the standard router; broken core swap flow |
| **Does** allowlist the router | Every non-allowlisted user bypasses the guard by routing through the router |

---

### Impact Explanation

A pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers) is fully bypassed the moment the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity, causing LP providers to trade with counterparties the pool was explicitly configured to exclude. This is a direct, fund-impacting breach of the pool's curation invariant: unauthorized swap execution drains LP value through trades the pool was designed to reject.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented swap interface for the protocol. Pool admins who deploy `SwapAllowlistExtension` will naturally allowlist the router so their permitted users can access the standard periphery. The bypass is then reachable by any public user with a single router call — no special privileges, no multi-step setup, no flash loan required.

---

### Recommendation

The extension must check the identity of the economic actor, not the immediate caller. Two sound approaches:

1. **Router-forwarded identity**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension` decodes and checks that address instead of `sender`.
2. **Pool-level originator field**: Add an explicit `originator` parameter to `pool.swap` that the router populates with `msg.sender`. The pool forwards it to extensions alongside `sender`. Extensions that need to gate the economic actor check `originator`.

Either approach must be consistent across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and their recursive callback paths.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as extension1.
2. Pool admin calls setAllowedToSwap(pool, alice, true)      // allowlist Alice
3. Pool admin calls setAllowedToSwap(pool, router, true)     // allowlist router so Alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, zeroForOne: true, amountIn: X, ...})
5. Router calls pool.swap(recipient, true, X, ...) — msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
8. Bob's swap executes successfully despite not being allowlisted.
``` [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L121-125)
```text
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
