### Title
Slow-Mode `WithdrawCollateral` Hardcodes Recipient to Subaccount-Embedded Address, Permanently Locking Funds for Account Abstraction Wallet Users — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The slow-mode `WithdrawCollateral` transaction path in `EndpointTx.processSlowModeTransactionImpl()` hardcodes `sendTo = address(0)`, which forces `Clearinghouse.withdrawCollateral()` to always send withdrawn funds to the address embedded in the subaccount `bytes32`. Users who deposited to Nado using an account abstraction wallet address from another chain (e.g., a Safe/Gnosis wallet) and manage their subaccount via a linked signer cannot redirect their withdrawal to an address they actually control on the Nado deployment chain, resulting in permanent loss of funds.

---

### Finding Description

Nado subaccounts are `bytes32` values whose first 20 bytes encode the owner address and the remaining 12 bytes encode a subaccount name. The protocol exposes two deposit entry points:

**1. `Endpoint.depositCollateral()`** constructs the subaccount from `msg.sender`, so the embedded address is always the caller's address on the current chain.

**2. `Endpoint.depositCollateralWithReferral()`** accepts an **arbitrary** `bytes32 subaccount` with no check that `msg.sender` matches the embedded address: [1](#0-0) 

This means any caller can deposit funds into a subaccount whose embedded address is any arbitrary address — including an address the depositor controls on another chain but not on the Nado chain.

**3. The slow-mode `WithdrawCollateral` struct** (the only slow-mode withdrawal type) has no `sendTo` field: [2](#0-1) 

**4. `EndpointTx.processSlowModeTransactionImpl()`** processes this transaction type by hardcoding `sendTo = address(0)`: [3](#0-2) 

**5. `Clearinghouse.withdrawCollateral()`** resolves `address(0)` by falling back to the address embedded in the subaccount bytes32: [4](#0-3) 

There is no mechanism in the slow-mode path for the user to override the recipient address. The fast-withdrawal `WithdrawCollateralV2` path in `BaseWithdrawPool` does support a `sendTo` override, but this is unavailable for slow-mode withdrawals: [5](#0-4) 

---

### Impact Explanation

A user with an account abstraction wallet (e.g., Safe/Gnosis) whose address on Ethereum mainnet is `A` will have a **different** address `A'` on the Nado deployment chain (Ink, `chainid 57073`) unless the Safe was deployed with the exact same factory and salt. If the user:

1. Deposits to Nado via `depositCollateralWithReferral` using their mainnet Safe address `A` as the subaccount,
2. Sets up a linked signer (an EOA key `B` they control on Ink) to manage the subaccount,
3. Signs and submits a slow-mode `WithdrawCollateral` transaction using key `B`,

then `Clearinghouse.withdrawCollateral()` sends the funds to address `A` on Ink chain — an address the user does not control. The funds are permanently lost with no rescue path, since no admin function can recover collateral credited to an arbitrary subaccount and sent to an uncontrolled address. [6](#0-5) 

---

### Likelihood Explanation

- Safe/Gnosis wallets are the dominant account abstraction wallet with millions of users and hundreds of billions in assets under management.
- `depositCollateralWithReferral` is a public, permissionless function that accepts any `bytes32 subaccount` — no validation that `msg.sender` matches the embedded address.
- The `LinkSigner` mechanism is explicitly designed to allow a separate key to manage a subaccount, making the scenario (deposit with cross-chain address, manage via linked signer, withdraw) a natural and documented usage pattern.
- Nado is deployed on Ink chain (`chainid 57073`), a chain where Safe factory deployments may not be uniformly available, increasing the probability of address divergence. [7](#0-6) 

---

### Recommendation

Add a `sendTo` field to the `WithdrawCollateral` struct (or introduce a `WithdrawCollateralV2` slow-mode transaction type) that allows the user to specify a recipient address on the current chain. Mirror the logic already present in the fast-withdrawal path:

```solidity
address resolvedSendTo = txn.sendTo == address(0)
    ? address(uint160(bytes20(txn.sender)))
    : txn.sendTo;
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, resolvedSendTo, nSubmissions
);
```

Additionally, consider adding a front-end warning for account abstraction wallet holders when using `depositCollateralWithReferral` with a cross-chain address. [8](#0-7) 

---

### Proof of Concept

1. Alice has a Safe wallet on Ethereum mainnet at address `A`. On Ink chain, address `A` is either undeployed or controlled by a different party.
2. Alice calls `Endpoint.depositCollateralWithReferral(subaccount_A, productId, amount, referral)` on Ink chain, where `subaccount_A = bytes32(abi.encodePacked(A, bytes12(0)))`. Funds are credited to subaccount `A`.
3. Alice calls `Endpoint.submitSlowModeTransaction(LinkSigner{sender: subaccount_A, signer: B, nonce: 0})` to authorize her EOA key `B` on Ink chain.
4. Alice signs and submits `WithdrawCollateral{sender: subaccount_A, productId: productId, amount: amount, nonce: 1}` using key `B`.
5. `EndpointTx.processSlowModeTransactionImpl()` calls `clearinghouse.withdrawCollateral(subaccount_A, productId, amount, address(0), nSubmissions)`.
6. `Clearinghouse.withdrawCollateral()` resolves `sendTo = address(uint160(bytes20(subaccount_A))) = A`.
7. Funds are transferred to address `A` on Ink chain — an address Alice does not control.
8. Funds are permanently lost. No `rescueTokens` or admin function can recover them from the recipient address. [3](#0-2) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

**File:** core/contracts/Endpoint.sol (L123-131)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));
```

**File:** core/contracts/interfaces/IEndpoint.sol (L80-85)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
    }
```

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
```
