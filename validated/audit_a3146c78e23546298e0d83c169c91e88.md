### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool sets `sender = msg.sender`, which is the `MetricOmmSimpleRouter` address when a user routes through the periphery. If the pool admin allowlists the router (the only way to let allowlisted users trade via the router), every non-allowlisted user can bypass the gate by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the curated-pool gate as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument the pool passes to `_beforeSwap`. The pool's `swap` function passes its own `msg.sender` — the direct caller — as `sender`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L151-176
function _beforeSwap(
    address sender,      // ← pool's msg.sender, i.e. the router
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
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, zeroForOne, amountSpecified,
             priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
    );
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)`. The pool sees `msg.sender = router` and passes `sender = router` to the extension. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also bypass the gate** |

There is no configuration that simultaneously allows allowlisted users to trade via the router and blocks non-allowlisted users from doing the same. [3](#0-2) 

---

### Impact Explanation

Any user not on the allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or any other public router entry point) targeting a curated pool. If the router is allowlisted — the only way to make the router usable for legitimate users — the extension's `allowedSwapper` check resolves to `true` for the router address, and the non-allowlisted user's swap executes without restriction. This directly breaks the curation invariant: disallowed actors can drain liquidity, extract fees, or trade against oracle prices on pools that were explicitly restricted to a vetted set of counterparties.

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router, which is the natural and expected operational step for any curated pool that intends to support the protocol's own periphery. No privileged access, no malicious setup, and no non-standard token behavior is needed. Any public user can then exploit the bypass with a single router call.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediate router. Two sound approaches:

1. **Check `recipient` instead of `sender`** when the pool's swap convention is that `recipient` is the beneficiary of the output tokens (i.e., the real user). This requires confirming the pool's semantic for `recipient`.

2. **Have the router forward the originating user's address** as an explicit `sender` field inside `extensionData`, and have the extension decode and verify that address. The router would sign or attest the real caller so the extension can gate on it.

Either way, the allowlist mapping key must be the address that the pool admin intends to gate, not the address of the public intermediary contract.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ..., bob_as_recipient, ...)`.
5. The router calls `pool.swap(bob_as_recipient, ...)`. The pool passes `sender = router` to `_beforeSwap`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → no revert.
7. Bob's swap executes on the curated pool despite never being allowlisted. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-176)
```text
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
```
