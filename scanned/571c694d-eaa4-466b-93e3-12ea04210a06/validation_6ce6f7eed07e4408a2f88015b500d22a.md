### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`. The pool's swap logic supplies `msg.sender` (the direct caller of `pool.swap()`) as `sender`. When any user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the curated-pool gate by going through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the only caller of the hook). `sender` is the address the pool forwards from its own `swap()` call — which is `msg.sender` inside the pool, i.e., the **direct caller** of `pool.swap()`. [1](#0-0) 

The pool's internal `_beforeSwap` dispatcher passes this `sender` value straight through to the extension: [2](#0-1) 

The test suite confirms the identity model: the allowlisted address is `callers[0]` (the `TestCaller` contract that calls the pool directly), not `users[0]` (the economic recipient). The pool uses `msg.sender` as `sender`, not an explicit user-supplied parameter. [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()` on behalf of a user, the pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`. The actual user's address is never checked.

This creates an inescapable catch-22 for any pool that wants to use the allowlist with the router:

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | ❌ Blocked (broken UX) | ✓ Blocked |
| Yes | ✓ Allowed | ❌ **Bypass — allowed** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

Additionally, `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier that `BaseMetricExtension` declares on the same hook, meaning the extension can be called by any address — though the `msg.sender`-keyed lookup prevents direct exploitation of that gap in isolation. [4](#0-3) 

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict trading to KYC'd or institutional counterparties. To allow those users to interact via the standard router, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, **any address** — including completely non-allowlisted users — can execute swaps on the curated pool by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection. Funds in the pool are exposed to unrestricted trading, violating the pool's curation invariant and potentially enabling price manipulation or unauthorized extraction of LP value.

---

### Likelihood Explanation

The trigger is a single, non-malicious admin action: allowlisting the router so that legitimate users can trade through the standard periphery. This is the expected operational pattern for any pool that wants to support the protocol's own router. The bypass is reachable by any unprivileged user immediately after that configuration step, with no special tokens, flash loans, or multi-step setup required.

---

### Recommendation

The `sender` forwarded to the extension must represent the **economic actor**, not the intermediary contract. Two sound approaches:

1. **Explicit sender parameter in `pool.swap()`**: Add a `sender` parameter to the pool's public `swap()` function (analogous to Uniswap v4's `PoolManager`). The router passes `msg.sender` (the user) as `sender`. The pool forwards this value to the extension. The extension then checks the actual user.

2. **Extension-side router unwrapping**: Have the extension query the router for the originating user (e.g., via a transient-storage context slot the router writes before calling the pool). This keeps the pool interface unchanged but requires the router to cooperate.

Either way, the `onlyPool` modifier should be restored on `SwapAllowlistExtension.beforeSwap` to match the base contract's access control.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension in the beforeSwap slot.
2. Pool admin allowlists alice (a legitimate user):
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. bob (non-allowlisted) calls:
       router.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
5. Router calls pool.swap(recipient=bob, zeroForOne=..., ...)
6. Pool: sender = msg.sender = router
7. Extension checks: allowedSwapper[pool][router] == true  → passes
8. bob's swap executes on the curated pool despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [5](#0-4)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
