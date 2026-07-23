### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the direct caller of `pool.swap` (i.e., `msg.sender` inside the pool). When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the user. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every unpermissioned user can bypass the curated allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by the pool's `_beforeSwap` dispatcher. The pool passes its own `msg.sender` — the direct caller of `pool.swap` — as `sender`. [2](#0-1) 

The extension order machinery calls each configured extension with the same `data` blob, which encodes `sender` as the direct pool caller. [3](#0-2) 

The test suite confirms this binding: the allowlisted identity is `address(callers[0])` — the `TestCaller` contract that directly calls `pool.swap` — not `users[0]`, the EOA who initiated the action. [4](#0-3) 

When `MetricOmmSimpleRouter` is the entry point, the call chain is:

```
user EOA → MetricOmmSimpleRouter.exactInput*()
         → pool.swap(recipient, ...)   // msg.sender = router
         → _beforeSwap(router, recipient, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
         → allowedSwapper[pool][router]  ← checked, NOT the user
```

A pool admin who wants legitimate users to be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **any** caller of the router, including users who were never individually allowlisted.

The identical wrong-actor binding exists in `DepositAllowlistExtension.beforeAddLiquidity`: it checks `sender` (the direct caller of `pool.addLiquidity`) rather than `owner` (the position beneficiary). When `MetricOmmPoolLiquidityAdder` is used, `sender` = the adder contract, so allowlisting the adder opens deposits to every user. [5](#0-4) 

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only, or regulatory-restricted) relies on `SwapAllowlistExtension` to enforce that only approved addresses can trade. Once the router is allowlisted — a necessary step for approved users to use the standard periphery — the gate is open to every address. Any unpermissioned user can execute swaps against the pool's liquidity, violating the access-control invariant and potentially extracting value from LPs who deposited under the assumption of a restricted counterparty set. This is a direct loss of the pool's intended access-control guarantee and a medium-to-high impact on LP principal depending on pool size and spread configuration.

---

### Likelihood Explanation

The scenario is realistic and likely in production:

1. A pool admin deploys a curated pool with `SwapAllowlistExtension`.
2. The admin allowlists a set of approved user EOAs for direct pool calls.
3. The admin also allowlists `MetricOmmSimpleRouter` so approved users can use the standard router (multi-hop, ETH wrapping, slippage helpers).
4. Step 3 silently opens the pool to all users.

The router is a well-known, trusted, public contract. Allowlisting it is the natural operational step to support the standard periphery flow.

---

### Recommendation

The allowlist must gate the **economically relevant actor**, not the intermediary contract. Two viable approaches:

1. **Check `owner`/user passed via `extensionData`**: Have the router encode the originating user address in `extensionData`; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For swaps, the recipient is closer to the intended gated party, though it can also be a third-party address.

3. **Separate router-level allowlist from user-level allowlist**: The extension could maintain a two-tier check — `allowedRouter[pool][sender]` AND `allowedSwapper[pool][decodedUser]` — requiring both to pass.

The simplest safe fix is to never allowlist a public intermediary contract as a swapper; instead, require that every user call the pool directly if the pool is curated. This should be documented as a hard invariant for pool admins.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists approved user Alice for direct swaps
ext.setAllowedToSwap(pool, alice, true);
// Admin also allowlists the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (never allowlisted) routes through the router
// router.exactInputSingle calls pool.swap(recipient, ...)
// pool passes msg.sender = router as `sender` to the extension
// ext checks allowedSwapper[pool][router] == true  → passes
// Bob's swap executes on the curated pool
router.exactInputSingle(pool, zeroForOne, amountIn, minOut, bob, "");
// No revert — allowlist bypassed
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L31-51)
```text
  uint256 internal immutable AFTER_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before swap.
  uint256 internal immutable BEFORE_SWAP_ORDER;
  /// @dev Order of extension calls for after swap.
  uint256 internal immutable AFTER_SWAP_ORDER;

  constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    EXTENSION_2 = extensions.extension2;
    EXTENSION_3 = extensions.extension3;
    EXTENSION_4 = extensions.extension4;
    EXTENSION_5 = extensions.extension5;
    EXTENSION_6 = extensions.extension6;
    EXTENSION_7 = extensions.extension7;
    BEFORE_ADD_LIQUIDITY_ORDER = extensionOrders.beforeAddLiquidity;
    AFTER_ADD_LIQUIDITY_ORDER = extensionOrders.afterAddLiquidity;
    BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
    AFTER_REMOVE_LIQUIDITY_ORDER = extensionOrders.afterRemoveLiquidity;
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER = extensionOrders.afterSwap;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-66)
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
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-48)
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

  function test_onlyPoolAdminCanSetSwappers() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);
    assertTrue(extension.isAllowedToSwap(address(pool), swapper));

    vm.prank(swapper);
    vm.expectRevert(abi.encodeWithSelector(BaseMetricExtension.OnlyPoolAdmin.selector, address(pool), swapper, admin));
    extension.setAllowedToSwap(address(pool), swapper, false);
  }
```
