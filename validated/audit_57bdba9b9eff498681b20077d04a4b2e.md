### Title
Unprotected `creditDeposit()` Sweeps Entire DDA Token Balance to Fixed Subaccount — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`creditDeposit()` in `DirectDepositV1` is callable by any unprivileged external account and sweeps the **entire** ERC-20 balance of every registered product token held by the DDA contract into the immutably fixed `subaccount`. Any tokens present in the DDA from any source — including accidental third-party transfers — are permanently credited to the DDA's fixed subaccount with no mechanism to distinguish or protect pre-existing balances.

---

### Finding Description

`creditDeposit()` carries no access-control modifier:

```solidity
function creditDeposit() external {                          // no onlyOwner
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));   // entire balance
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount,                                  // fixed at construction
                productId,
                uint128(balance),                           // all of it
                "-1"
            );
        }
    }
}
``` [1](#0-0) 

The `subaccount` field is set once at construction and never changes: [2](#0-1) 

The function iterates over **all** product IDs returned by `spotEngine.getProductIds()` and deposits the **full** `balanceOf(address(this))` for each token. There is no snapshot of a "pre-existing" balance, no per-caller accounting, and no guard that limits the sweep to tokens that arrived in the current transaction.

The same trigger is also exposed through `ContractOwner.creditDepositV1`, which is itself unrestricted: [3](#0-2) 

---

### Impact Explanation

**Broken invariant:** The DDA is intended to act as a staging address for a single user's deposits. The implicit invariant is that only tokens sent by (or on behalf of) the DDA's owner are credited to `subaccount`. `creditDeposit()` violates this: it credits **every token present in the contract**, regardless of origin.

**Concrete asset delta:**

1. User A accidentally sends token T to `DDA_B` (a DDA whose `subaccount` belongs to User B).
2. Any caller (including User B or an unrelated third party) calls `creditDeposit()` on `DDA_B`.
3. `token.balanceOf(DDA_B)` includes User A's tokens; the full amount is approved and deposited to User B's `subaccount` via `depositCollateralWithReferral`.
4. User A's tokens are permanently credited to User B's on-chain subaccount balance in `SpotEngine`. User A has no recourse.

The `depositCollateralWithReferral` path in `Endpoint` pulls tokens from `msg.sender` (the DDA) and enqueues a `DepositCollateral` slow-mode transaction crediting `subaccount`: [4](#0-3) 

Once the slow-mode transaction executes, `Clearinghouse.depositCollateral` calls `spotEngine.updateBalance`, permanently increasing User B's balance: [5](#0-4) 

There is no reversal path for User A.

---

### Likelihood Explanation

- DDA addresses are deterministic (`salt: bytes32(uint256(1))`), so they are predictable and publicly discoverable.
- Users interacting with multiple subaccounts or copy-pasting addresses can easily send tokens to the wrong DDA.
- `creditDeposit()` requires zero privilege to call; any EOA or contract can trigger the sweep at any time, including immediately after an accidental transfer.
- The `ContractOwner.creditDepositV1` wrapper is also unrestricted, providing a second permissionless entry point. [6](#0-5) 

---

### Recommendation

1. **Add access control** to `creditDeposit()` — restrict it to `onlyOwner` (the `ContractOwner` contract) so that only the protocol can trigger the sweep.
2. **Alternatively**, record a per-token "expected balance" at the time tokens are sent (e.g., via a dedicated `receive`/`transfer` hook or a pre-deposit snapshot), and only deposit the delta above that baseline — mirroring the `startingBalance` pattern the LiFi fix applied.
3. Audit `ContractOwner.creditDepositV1` and `wrapVaultAsset` for the same missing access control, as both are `external` with no restriction.

---

### Proof of Concept

```
1. ContractOwner deploys DDA_B for subaccount B (User B).
   DDA_B address is deterministic and publicly known.

2. User A mistakenly sends 1000 USDC to DDA_B
   (e.g., copy-paste error, wrong address in UI).

3. Attacker (or anyone) calls:
       DDA_B.creditDeposit()
   OR
       ContractOwner.creditDepositV1(subaccountB)

4. creditDeposit() reads:
       balance = USDC.balanceOf(DDA_B)  // = 1000 USDC
   Approves endpoint for 1000 USDC.
   Calls endpoint.depositCollateralWithReferral(subaccountB, USDC_productId, 1000, "-1").

5. Endpoint pulls 1000 USDC from DDA_B, enqueues DepositCollateral slow-mode tx.

6. On execution: Clearinghouse.depositCollateral credits +1000 USDC to subaccountB
   in SpotEngine.

7. User A's 1000 USDC is permanently in User B's subaccount.
   User A has no on-chain recourse.
```

### Citations

**File:** core/contracts/DirectDepositV1.sol (L42-51)
```text
    constructor(
        address _endpoint,
        address _spotEngine,
        bytes32 _subaccount,
        address payable _wrappedNative
    ) {
        endpoint = IIEndpoint(_endpoint);
        spotEngine = IISpotEngine(_spotEngine);
        subaccount = _subaccount;
        wrappedNative = _wrappedNative;
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/ContractOwner.sol (L486-500)
```text
    function createDirectDepositV1(bytes32 subaccount)
        public
        returns (address payable)
    {
        require(
            getDirectDepositV1BytecodeHash() ==
                0x7974df41bdca2be1539fa7d01f41277f0d728823b20230a18a31e40c707874e7,
            "dda hash"
        );
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
        return payable(directDepositV1);
    }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/Endpoint.sol (L144-166)
```text
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
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Clearinghouse.sol (L193-208)
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
```
