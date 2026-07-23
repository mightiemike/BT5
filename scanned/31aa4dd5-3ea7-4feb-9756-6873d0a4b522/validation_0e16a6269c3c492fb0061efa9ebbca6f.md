### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Disallowed Users to Bypass the Swap Guard via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` (i.e., whoever called `pool.swap()`) as that `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore checks whether the **router** is permitted, not whether the **end user** is permitted. Any user who is individually disallowed can bypass the guard by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-L176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

The pool populates that `sender` slot with its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol (swap entrypoint)
_beforeSwap(msg.sender, recipient, ...);
```

When a user calls `MetricOmmSimpleRouter.exactInput*` or `exactOutput*`, the router calls `pool.swap(...)`, making the pool's `msg.sender` the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two broken outcomes follow:

1. **Router is allowlisted** (the common operational choice so that legitimate users can use the router): every user, including those explicitly blocked by the pool admin, can bypass the allowlist by routing through the public router.
2. **Router is not allowlisted**: the allowlist blocks the router entirely, so even individually allowlisted users cannot use the router — breaking core swap functionality for the intended audience.

The analog to M-07 is exact: a configured guard (`forSale = false` / `allowedSwapper = false`) is bypassed because an intermediate contract (Compensate / MetricOmmSimpleRouter) is the actor the protocol actually checks, not the economic principal the guard was meant to restrict.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market makers) finds the restriction completely ineffective. Any disallowed address can call `MetricOmmSimpleRouter` and trade freely. This constitutes a direct policy bypass with fund-impacting consequences: the pool receives tokens from and sends tokens to actors the admin explicitly excluded, violating the curation invariant and potentially exposing the pool to regulatory, counterparty, or MEV risk the allowlist was designed to prevent.

---

### Likelihood Explanation

- No privileged access is required. Any public user can call the router.
- The router is a standard, documented periphery entry point — it is the expected path for most users.
- The bypass requires zero preconditions beyond the pool having `SwapAllowlistExtension` configured and the router being allowlisted (or the user simply trying the router when direct swaps are blocked).
- The pool admin has no on-chain mechanism to distinguish router-mediated swaps from direct swaps without changing the extension design.

---

### Recommendation

The pool must forward the **original caller's identity** through the swap path so the extension can gate the economic actor, not the intermediary. Two approaches:

1. **Router passes original sender explicitly**: The router should pass the end user's address as a dedicated field in `extensionData`, and the extension should decode and check that field. However, this is fragile because it relies on the router's cooperation.

2. **Pool exposes an `initiator` parameter** (preferred): Add an `initiator` address to `pool.swap()` that the router populates with `msg.sender` before calling the pool. The pool forwards `initiator` (not `msg.sender`) as the `sender` argument to `_beforeSwap`. The extension then checks `allowedSwapper[pool][initiator]`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist Alice: allowedSwapper[pool][alice] = false

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, bid, ask, extensionData)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — Alice's trade settles despite being individually blocked

Result:
  Alice, a disallowed swapper, successfully trades on a curated pool.
  The SwapAllowlistExtension guard is silently bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
