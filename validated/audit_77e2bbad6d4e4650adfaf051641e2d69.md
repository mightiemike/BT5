### Title
Missing Received-Token Balance Check on Deposit Allows Fee-on-Transfer Token Inflation of Subaccount Credit — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer()` pulls tokens from the depositor and forwards them to the clearinghouse using the caller-supplied `amount` value directly, with no before/after balance check. The slow-mode `DepositCollateral` transaction is then queued with that same raw `amount`. When the sequencer settles it, `Clearinghouse.depositCollateral()` credits `amount × decimals_multiplier` to the subaccount — regardless of how many tokens were actually received. With a fee-on-transfer or rebasing token, the protocol credits more collateral than it holds, corrupting its solvency invariant.

---

### Finding Description

The deposit entry path is:

1. **`Endpoint.depositCollateralWithReferral()`** (lines 123–167) accepts a caller-controlled `amount`, calls `handleDepositTransfer(token, msg.sender, uint256(amount))`, then enqueues a `DepositCollateral` slow-mode transaction carrying the same `amount`.

2. **`EndpointStorage.handleDepositTransfer()`** (lines 111–119) executes two transfers in sequence — `safeTransferFrom(token, from, amount)` then `safeTransferTo(token, clearinghouse, amount)` — with no balance snapshot before or after either call.

3. **`Clearinghouse.depositCollateral()`** (lines 193–209) reads `txn.amount` from the queued transaction and computes `amountRealized = int128(txn.amount) * int128(multiplier)`, crediting that full value to the subaccount via `spotEngine.updateBalance()`.

At no point is the actual received balance compared against `amount`. The protocol unconditionally trusts the caller-supplied figure.

```
handleDepositTransfer(token, msg.sender, uint256(amount))
  safeTransferFrom(token, from, address(this), amount)   // may deliver < amount
  safeTransferTo(token, address(clearinghouse), amount)  // forwards full amount
// DepositCollateral queued with original `amount`
// Clearinghouse credits `amount * multiplier` — no receipt check
``` [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

For any ERC-20 token registered as a spot product that implements a transfer fee or rebase-down mechanic:

- The Endpoint/Clearinghouse token pool holds `amount - fee` tokens.
- The subaccount is credited `amount` (normalized) of collateral.
- The delta (`fee` worth of collateral) is phantom credit — it is backed by nothing.
- Repeated deposits inflate the subaccount's apparent collateral, allowing the attacker to open larger positions or withdraw more than was deposited, draining real assets from other depositors.
- At scale this breaks the protocol's solvency invariant: total on-chain token holdings < sum of credited subaccount balances. [4](#0-3) 

---

### Likelihood Explanation

The trigger requires only that a fee-on-transfer or rebasing token be listed as a supported spot product — a configuration decision made by the protocol deployer, not the attacker. Once such a token exists, any unprivileged user calling `depositCollateral` or `depositCollateralWithReferral` (both are public/external with no access control beyond sanctions checks) can exploit the discrepancy. The attacker needs no special role, no governance capture, and no leaked keys. [5](#0-4) 

---

### Recommendation

Capture the clearinghouse's token balance before and after the transfer pair inside `handleDepositTransfer`, and use the measured delta — not the caller-supplied `amount` — as the value enqueued in the slow-mode `DepositCollateral` transaction:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 received) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    received = token.balanceOf(address(clearinghouse)) - before;
    require(received == amount, "fee-on-transfer token not supported");
    // or: use `received` as the credited amount instead of `amount`
}
```

The queued `DepositCollateral.amount` must then be set to the measured `received` value, not the original `amount` argument. [1](#0-0) 

---

### Proof of Concept

1. Deploy a mock ERC-20 token with a 10% fee-on-transfer; register it as a spot product.
2. Call `Endpoint.depositCollateralWithReferral(subaccount, productId, 1000, "")`.
3. `handleDepositTransfer` pulls 1000 from the caller; the token delivers 900 to the Endpoint, then 900 to the clearinghouse (second `safeTransfer` reverts if the Endpoint holds exactly 900 — but if the Endpoint already holds a residual balance the full 1000 goes through, or the second leg also takes a fee delivering 810).
4. The slow-mode queue records `amount = 1000`.
5. Sequencer calls `executeSlowModeTransaction` → `Clearinghouse.depositCollateral` → `spotEngine.updateBalance(productId, subaccount, 1000 * multiplier)`.
6. Subaccount now shows 1000 units of collateral; clearinghouse holds ≤ 900 tokens.
7. Attacker withdraws 1000 units, draining 100 tokens that belonged to other depositors. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Endpoint.sol (L123-165)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```
