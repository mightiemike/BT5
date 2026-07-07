### Title
Sanctioned Address Bypasses Sanctions Check via `DirectDepositV1` Intermediary to Deposit Tainted Funds — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` is a permissionless, access-control-free external function that calls `Endpoint.depositCollateralWithReferral()` with `msg.sender` equal to the DDA contract address. The sanctions enforcement in `depositCollateralWithReferral` checks only the immediate caller (`msg.sender` = DDA, not sanctioned) and the address embedded in the stored `subaccount` bytes32 — neither of which is required to be the actual token source. A sanctioned address can deploy a DDA, fund it with tainted tokens, and trigger `creditDeposit()` to launder those tokens into the Nado clearinghouse, bypassing all sanctions checks.

---

### Finding Description

`Endpoint.depositCollateralWithReferral` enforces two sanctions checks:

```solidity
requireUnsanctioned(msg.sender);
requireUnsanctioned(sender); // address(bytes20(subaccount))
``` [1](#0-0) 

The actual token pull is from `msg.sender`:

```solidity
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
``` [2](#0-1) 

`DirectDepositV1.creditDeposit()` is `external` with no access control. It calls `depositCollateralWithReferral` with `msg.sender` = the DDA contract address and `subaccount` = whatever was set in the constructor:

```solidity
function creditDeposit() external {
    ...
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),
        "-1"
    );
``` [3](#0-2) 

The DDA constructor accepts any arbitrary `_subaccount` bytes32 with no validation that `address(bytes20(_subaccount))` matches the deployer or owner:

```solidity
constructor(
    address _endpoint,
    address _spotEngine,
    bytes32 _subaccount,
    address payable _wrappedNative
) {
    ...
    subaccount = _subaccount;
``` [4](#0-3) 

Once the slow-mode `DepositCollateral` transaction is queued, `Clearinghouse.depositCollateral` processes it with **no sanctions check**:

```solidity
function depositCollateral(IEndpoint.DepositCollateral calldata txn)
    external
    virtual
    onlyEndpoint
{
    ...
    spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [5](#0-4) 

---

### Impact Explanation

A sanctioned address's tainted tokens enter the Nado clearinghouse and are credited to a subaccount the sanctioned address controls (via a non-sanctioned proxy address in the subaccount bytes32). The protocol's core invariant — that sanctioned addresses cannot deposit funds — is fully violated. The tainted funds are now indistinguishable from legitimate collateral inside the system and can be used for trading, earning yield, or withdrawal to a clean address.

---

### Likelihood Explanation

The attack requires only:
1. Deploying a `DirectDepositV1` contract (permissionless, no admin required)
2. Transferring tokens to it (standard ERC20 transfer)
3. Calling `creditDeposit()` (public, no access control)

No privileged access, no governance capture, no leaked keys. Any sanctioned address with on-chain tokens can execute this immediately. The `DirectDepositV1` contract is a production peripheral contract explicitly designed to accept tokens and forward them to the endpoint.

---

### Recommendation

1. **Add a sanctions check on the token source in `creditDeposit()`**: Before calling `depositCollateralWithReferral`, `DirectDepositV1` should verify that neither the DDA owner nor the subaccount owner is sanctioned.
2. **Validate subaccount ownership in the DDA constructor**: Require `address(bytes20(_subaccount)) == msg.sender` (or the owner) so the DDA cannot be configured to credit an arbitrary address.
3. **Add a sanctions check in `Clearinghouse.depositCollateral`**: As a defense-in-depth measure, re-check the subaccount owner for sanctions at the point of balance credit, not only at deposit submission time.

---

### Proof of Concept

```solidity
// Sanctioned address executes this sequence:

// 1. Deploy DDA with a fresh non-sanctioned address as the subaccount owner
address freshWallet = makeAddr("freshWallet"); // not sanctioned
bytes32 craftedSubaccount = bytes32(abi.encodePacked(freshWallet, bytes12(0)));

DirectDepositV1 dda = new DirectDepositV1(
    address(endpoint),
    address(spotEngine),
    craftedSubaccount,
    wrappedNative
);

// 2. Transfer tainted tokens to the DDA
token0.transfer(address(dda), 1 ether);

// 3. Call creditDeposit() — msg.sender to endpoint is DDA (not sanctioned)
//    requireUnsanctioned(msg.sender) → DDA address ✓
//    requireUnsanctioned(sender)     → freshWallet ✓
//    handleDepositTransfer pulls from DDA (funded by sanctioned address)
dda.creditDeposit();

// 4. Slow-mode tx is queued; when processed, clearinghouse.depositCollateral
//    credits freshWallet's subaccount with no further sanctions check.
// Tainted funds are now inside the protocol.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L123-167)
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
        slowModeConfig = _slowModeConfig;
    }
```

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

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
