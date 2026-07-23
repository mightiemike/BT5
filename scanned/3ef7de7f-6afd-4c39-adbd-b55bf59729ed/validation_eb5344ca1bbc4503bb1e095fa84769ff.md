### Title
`DepositAllowlistExtension` gates the wrong actor (`owner` instead of `sender`), allowing any unauthorized address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces its allowlist check against the `owner` parameter (the LP-share recipient) rather than the first unnamed parameter (the actual caller / token provider). Because `owner` is an arbitrary address supplied by the caller, any unauthorized address can bypass the deposit gate by naming an already-allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters: the first (unnamed) is the `sender` — the address that actually called `pool.addLiquidity` and will provide the tokens — and the second is `owner`, the LP-position owner who will receive the minted shares. [1](#0-0) 

The guard silently ignores the first parameter and only checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a caller-controlled argument to `pool.addLiquidity`. An unauthorized address can call:

```solidity
pool.addLiquidity(
    owner = <any allowlisted address>,   // passes the guard
    salt  = <any salt>,
    deltas = <desired liquidity>,
    extensionData = "",
    callbackData  = ""
);
```

The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` and returns the valid selector. The pool proceeds to mint LP shares to the allowlisted address while the unauthorized caller supplies the tokens. The deposit allowlist — the sole curation mechanism for the pool — is fully bypassed.

The `SwapAllowlistExtension` does not share this flaw; it correctly checks `sender` (the first parameter), confirming the asymmetry is specific to the deposit path. [2](#0-1) 

The pool's `addLiquidity` call signature, as exercised in tests, confirms that `owner` is an explicit caller-supplied argument, not a pool-derived identity: [3](#0-2) 

---

### Impact Explanation

A pool admin deploys a curated pool with `DepositAllowlistExtension` to restrict liquidity provision to a known set of addresses (e.g., KYC-verified LPs). Any unauthorized address can inject liquidity into the pool by attributing the position to an allowlisted address. Consequences:

- The pool's curation invariant is broken: unauthorized token flows enter the pool.
- The allowlisted `owner` receives unexpected LP shares (which they can later withdraw, extracting value from the pool's fee accrual).
- Pool composition and fee distribution are affected by liquidity the admin never authorized.
- The deposit allowlist — the admin-configured protection boundary — is rendered ineffective by an unprivileged path.

This matches the Allowed Impact Gate criterion: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."*

---

### Likelihood Explanation

The bypass requires only a standard `pool.addLiquidity` call with a known allowlisted address as `owner`. No privileged access, flash loan, or special token behavior is needed. Any address that can observe the allowlist (via `allowedDepositor` view) and hold the pool's tokens can execute this. Likelihood is **high**.

---

### Recommendation

Change `beforeAddLiquidity` to check the first parameter (the actual caller / token provider) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender`. [1](#0-0) 

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `allowedLP` is allowlisted.
address allowedLP   = makeAddr("allowedLP");
address attacker    = makeAddr("attacker");

vm.prank(admin);
extension.setAllowedToDeposit(address(pool), allowedLP, true);

// Attacker holds token0/token1 and calls addLiquidity naming allowedLP as owner.
vm.startPrank(attacker);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Extension checks allowedDepositor[pool][allowedLP] == true → passes.
// Attacker's tokens enter the pool; allowedLP receives LP shares.
pool.addLiquidity(
    allowedLP,          // owner — passes the guard
    SOME_SALT,
    deltas,
    "",
    ""
);
// Deposit succeeds despite attacker never being allowlisted.
```

### Citations

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

**File:** metric-core/test/MetricOmmPool.extensions.t.sol (L226-240)
```text
  function test_addLiquidity_lengthMismatch_doesNotCallBeforeExtension() public {
    MockMetricExtension extension = new MockMetricExtension();
    _deployPoolWithExtension(address(extension), _extensionOrdersWithBeforeAddLiquidity());
    extension.bindPool(address(pool));

    int256[] memory binIdxs = new int256[](1);
    binIdxs[0] = 0;
    uint256[] memory shares = new uint256[](2);
    shares[0] = 1000;
    shares[1] = 1000;
    LiquidityDelta memory deltas = LiquidityDelta({binIdxs: binIdxs, shares: shares});

    vm.expectRevert(IMetricOmmPoolActions.LiquidityDeltaLengthMismatch.selector);
    pool.addLiquidity(users[0], EXTENSION_TEST_SALT, deltas, "", "");
    assertFalse(extension.calledBeforeAddLiquidity());
```
