### Title
Unprotected `createDirectDepositV1` Allows Attacker to Hijack DDA Subaccount Recipient and Steal Deposited Collateral - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.createDirectDepositV1` is declared `public` with no access control. Because it deploys the `DirectDepositV1` contract via CREATE2 with a **hardcoded salt** (`bytes32(uint256(1))`), only one DDA can ever exist per `ContractOwner` instance. An unprivileged attacker can call this function first, binding the single possible DDA address to an attacker-controlled subaccount. All collateral subsequently sent to that DDA address is then credited to the attacker's subaccount, and no legitimate DDA can ever be created.

---

### Finding Description

`createDirectDepositV1` in `ContractOwner.sol` is marked `public` with no `onlyOwner`, `onlyDeployer`, or any other access guard: [1](#0-0) 

The function deploys `DirectDepositV1` using CREATE2 with a salt that is always `bytes32(uint256(1))`, regardless of the `subaccount` argument: [2](#0-1) 

Because the CREATE2 address is determined by `(deployer=ContractOwner, salt=1, bytecode=DirectDepositV1)`, it is fully deterministic and unique. Only one deployment is ever possible. The `subaccount` argument is passed to the `DirectDepositV1` constructor and stored as the permanent deposit recipient: [3](#0-2) 

`creditDeposit()` on the DDA is also unprotected — any caller can trigger it — and it deposits all token balances held by the DDA into the stored `subaccount`: [4](#0-3) 

The downstream `creditDepositV1` entry point also has no access control, and it calls `createDirectDepositV1` lazily if the DDA has not yet been deployed: [5](#0-4) 

---

### Impact Explanation

An attacker who calls `createDirectDepositV1(attackerSubaccount)` before any legitimate user:

1. Deploys the DDA at the one and only possible CREATE2 address, with `attackerSubaccount` as the permanent collateral recipient.
2. Permanently blocks all future legitimate DDA creation — any subsequent call to `createDirectDepositV1` (for any subaccount) will revert because the CREATE2 address is already occupied.
3. Any user who sends tokens to the DDA address (the standard deposit flow) will have those tokens credited to the attacker's subaccount when `creditDeposit()` is called.

Corrupted state: `directDepositV1Address[attackerSubaccount]` is set to the DDA, and the DDA's internal `subaccount` field permanently points to the attacker. All deposited collateral balances are misdirected.

---

### Likelihood Explanation

The attack requires a single permissionless transaction to `ContractOwner.createDirectDepositV1`. No special role, leaked key, or governance capture is needed. The function is callable by any EOA or contract. A front-running bot monitoring the mempool for the first legitimate `createDirectDepositV1` or `creditDepositV1` call can reliably execute this before the intended caller.

---

### Recommendation

Add `onlyOwner` (or `onlyDeployer`) to `createDirectDepositV1`:

```solidity
function createDirectDepositV1(bytes32 subaccount)
    public
    onlyOwner   // <-- add this
    returns (address payable)
```

Additionally, consider deriving the CREATE2 salt from the `subaccount` argument so that each subaccount maps to a distinct DDA address, eliminating the single-deployment constraint entirely.

---

### Proof of Concept

```solidity
// Attacker calls this before any legitimate user
contractOwner.createDirectDepositV1(attackerSubaccount);
// DDA is now deployed at address X, permanently bound to attackerSubaccount.

// Victim sends USDC to address X (the DDA), expecting it to credit their account.
usdc.transfer(ddaAddress, 1_000e6);

// Anyone (including attacker) calls creditDeposit — no access control.
DirectDepositV1(ddaAddress).creditDeposit();
// 1,000 USDC is deposited into attackerSubaccount, not the victim's.

// Any future attempt by a legitimate user to create their own DDA reverts:
contractOwner.createDirectDepositV1(victimSubaccount); // reverts: contract already at CREATE2 address
``` [1](#0-0) [4](#0-3)

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L42-52)
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
        uint256 balance = address(this).balance;
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
