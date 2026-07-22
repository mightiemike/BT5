### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the address that will hold the LP position) against the allowlist, while silently ignoring the `sender` parameter (the actual external caller). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unpermissioned address can bypass the deposit guard by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is the real caller (`msg.sender`); the second is the `owner` of the position. `DepositAllowlistExtension.beforeAddLiquidity` receives these in the same order but discards the first:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (the actual depositor/sender) is unnamed and never read. The guard only checks whether `owner` is allowlisted.

The contract's own NatSpec and admin setter name the gated entity "depositor":

```solidity
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
...
function setAllowedToDeposit(address pool_, address depositor, bool allowed) ...
``` [3](#0-2) 

The sibling `SwapAllowlistExtension` correctly reads the first parameter as `sender` and checks it:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The asymmetry confirms the deposit extension has the wrong parameter.

`addLiquidity` has no `msg.sender == owner` requirement (only `removeLiquidity` enforces that):

```solidity
function removeLiquidity(...) external ... {
    ...
    if (msg.sender != owner) revert NotPositionOwner();
``` [5](#0-4) 

So any address can call `addLiquidity(owner = allowlisted_address, ...)` and the guard passes.

---

### Impact Explanation

**Direct impacts:**

1. **Allowlist bypass / unauthorized pool participation.** A pool admin deploys a permissioned pool (e.g., KYC-only LPs). Any non-allowlisted address can deposit by setting `owner` to any allowlisted address. The attacker provides the tokens via the callback; the position is credited to the allowlisted `owner`. The attacker has effectively injected liquidity into a private pool.

2. **Bin-balance manipulation in permissioned pools.** By depositing into specific bins, the attacker shifts `token0BalanceScaled`/`token1BalanceScaled` and `binTotals`, altering the effective price curve and LP share values for all existing LPs in the pool.

3. **OracleValueStopLossExtension watermark poisoning.** The stop-loss extension reads bin balances after every swap to update per-bin high watermarks. Unauthorized deposits that inflate bin balances can raise watermarks, making the stop-loss guard harder to trigger and leaving LPs exposed to larger drawdowns than the configured `drawdownE6` threshold.

4. **Griefing allowlisted LPs.** An attacker can force unwanted liquidity positions onto an allowlisted address (e.g., locking tokens in bins the victim did not choose), since the victim cannot remove liquidity they did not add (they hold the shares but did not initiate the deposit).

---

### Likelihood Explanation

**High.** The preconditions are minimal:

- The pool must have `DepositAllowlistExtension` configured (a production extension).
- At least one allowlisted address must exist (required for the pool to be usable at all).
- The attacker needs only to know any allowlisted address and call `addLiquidity` with `owner` set to it.

No privileged access, no flash loan, no oracle manipulation is required. The call is a standard `addLiquidity` with a chosen `owner` argument.

---

### Recommendation

Rename the first parameter and check `sender` instead of `owner`, matching the intent stated in the NatSpec and the pattern used by `SwapAllowlistExtension`:

```diff
-function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
     external view override returns (bytes4)
 {
-    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
         revert IMetricOmmPoolActions.NotAllowedToDeposit();
     }
     return IMetricOmmExtensions.beforeAddLiquidity.selector;
 }
``` [2](#0-1) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Bob] = true
  Alice is NOT allowlisted

Attack:
  Alice calls pool.addLiquidity(
      owner    = Bob,       // allowlisted → check passes
      salt     = 0,
      deltas   = { binIdxs: [0], shares: [1e18] },
      callbackData = ...,   // Alice transfers tokens in callback
      extensionData = ""
  )

Result:
  beforeAddLiquidity(sender=Alice, owner=Bob) is called
  allowedDepositor[pool][Bob] == true → no revert
  Alice's callback transfers tokens to the pool
  Bob's position is credited with 1e18 shares in bin 0
  Alice has deposited into a pool she is not authorized to access
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-21)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
