### Title
SwapAllowlistExtension Bypassed via Router: Any Unprivileged User Can Swap on Allowlisted Pools — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the allowlist checks the **router's address**, not the end user's address. If the pool admin allowlists the router (required for any router-mediated swap to succeed on an allowlisted pool), every unprivileged user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Pool `swap` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← always the immediate caller of pool.swap()
  recipient,
  ...
);
```

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`:** [2](#0-1) 

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the router calls the pool, `sender = router`. The extension therefore checks whether the **router** is allowlisted, not the end user.

**The extension is wired into the before-swap hook order at pool construction:** [3](#0-2) 

The `_callExtensionsInOrder` dispatcher forwards the pool-supplied `sender` verbatim to every configured extension, so there is no point in the call chain where the original EOA address is recovered.

**The pool's swap callback is also issued to `msg.sender` (the router), confirming the router is the sole on-chain identity the pool sees:** [4](#0-3) 

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of users (e.g., KYC'd counterparties) deploys the pool with `SwapAllowlistExtension` and allowlists those users. To let those same users trade via the public router, the admin must also call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, **any address** can call `MetricOmmSimpleRouter.exactInput/exactOutput`, which calls `pool.swap()` with `msg.sender = router`, and the extension passes unconditionally. The per-user allowlist is entirely defeated.

Concrete consequence: an allowlisted pool intended for institutional-only flow is now open to arbitrary retail or adversarial traders. Those traders can extract value by front-running the institutional flow, draining LP inventory at oracle mid, or simply trading in ways the pool admin explicitly prohibited. LP principal is at risk because the pool's liquidity was sized and priced for a controlled counterparty set.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical periphery entry point; most users interact with pools through it.
- Any pool admin who enables both a swap allowlist and router access (a natural combination) creates the bypass automatically.
- No special privilege, flash loan, or oracle manipulation is required — a plain router call suffices.
- The bypass is silent: no event distinguishes a router-mediated swap from a direct one.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller. Two viable approaches:

1. **Propagate the originating user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before forwarding to the pool. `SwapAllowlistExtension` decodes and checks that address. This requires a trusted router convention and is fragile if other routers are added.

2. **Check `sender` against a router registry and then verify the user inside the extension**: The extension recognises known routers and, for router calls, requires the user address to be supplied and verified (e.g., via a signed payload or a transient-storage forwarding pattern).

3. **Document and enforce that the router must never be allowlisted on per-user-gated pools**: Treat the router as a separate pool type (`allowAllSwappers = true`) rather than adding it to a per-user list.

---

### Proof of Concept

```
Setup
─────
1. Admin deploys pool with SwapAllowlistExtension as BEFORE_SWAP extension.
2. Admin calls setAllowedToSwap(pool, Alice, true)   // Alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so Alice can use the router

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:            pool,
           recipient:       Bob,
           amountIn:        X,
           ...
       })

5. Router calls pool.swap(recipient=Bob, ...) → msg.sender = router

6. Pool calls _beforeSwap(sender=router, ...)

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true   ✓

8. Swap executes. Bob receives output tokens.
   The per-user allowlist never checked Bob's address.
```

The invariant `allowedSwapper[pool][Bob] == false` is broken: Bob swaps successfully on a pool that explicitly did not allowlist him.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L258-263)
```text
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
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
