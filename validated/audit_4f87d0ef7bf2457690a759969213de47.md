### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain from DirectDepositV1 Accounts — (File: `core/contracts/ContractOwner.sol`)

### Summary
`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its boolean return value. Because the function has no access control beyond a chain ID check, any unprivileged caller on chain 57073 can invoke it. If the USDC token returns `false` on a failed transfer rather than reverting, execution continues: the DDA's entire usdcE balance is withdrawn to `ContractOwner` and then forwarded to the caller — with no USDC ever actually deposited.

### Finding Description
`replaceUsdcEWithUsdc` is an `external` function with no `onlyOwner` or `onlyDeployer` guard. Its only gate is `require(block.chainid == 57073, ERR_UNAUTHORIZED)`. [1](#0-0) 

The intended flow is:
1. Pull `balance` of USDC from `msg.sender` into the DDA.
2. Withdraw usdcE from the DDA back to `ContractOwner`.
3. Forward that usdcE to `msg.sender`.

The raw `transferFrom` at line 616 returns a `bool` per `IERC20Base`, but the return value is silently discarded: [2](#0-1) 

`IERC20Base.transferFrom` is declared to return `bool`: [3](#0-2) 

The project already has `ERC20Helper.safeTransferFrom`, which correctly checks the return value and reverts on failure: [4](#0-3) 

`safeTransferFrom` is used elsewhere via `using ERC20Helper for IERC20Base` declared in `ContractOwner`, but was not applied here. [5](#0-4) 

`DirectDepositV1.withdraw` transfers the full usdcE balance of the DDA to its owner (`ContractOwner`), then `ContractOwner` forwards it to `msg.sender`: [6](#0-5) 

### Impact Explanation
If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (chain 57073) returns `false` on a failed transfer (insufficient allowance or balance) rather than reverting, an attacker with zero USDC and zero allowance can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds usdcE.
2. The `transferFrom` silently fails (returns `false`), no USDC moves.
3. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, pulling all usdcE from the DDA to `ContractOwner`.
4. `safeTransfer(msg.sender, balance)` sends all usdcE to the attacker.

The attacker receives the full usdcE balance of the targeted DDA at zero cost. The DDA's usdcE — which represents user-deposited collateral awaiting credit — is permanently stolen.

### Likelihood Explanation
The function is callable by any address on chain 57073 with no further restriction. The USDC token at the hardcoded address is a bridged/wrapped variant whose exact revert-vs-return-false behavior on failure must be verified; if it returns `false` on insufficient allowance (as some non-standard ERC20s do), the exploit is immediately reachable by any user. Even if the current token reverts, the unchecked pattern is a latent risk if the token is ever upgraded or replaced.

### Recommendation
Replace the raw `transferFrom` call with the project's existing `safeTransferFrom` helper, consistent with how all other token transfers in the codebase are handled:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` in `ERC20Helper` uses a low-level `call` and requires `success && (data.length == 0 || abi.decode(data, (bool)))`, which correctly handles both reverting and return-false token implementations. [4](#0-3) 

### Proof of Concept
1. A DDA for `subaccount` exists on chain 57073 and holds 1000 usdcE (user collateral awaiting credit).
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC balance and zero allowance.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` returns `false` — no USDC moves.
4. Return value is not checked; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers 1000 usdcE from the DDA to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` transfers 1000 usdcE to the attacker.
7. Attacker has drained 1000 usdcE from the DDA; the legitimate user's collateral is gone.

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
