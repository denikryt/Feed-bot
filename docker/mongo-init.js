const appUser = process.env.MONGO_APP_USERNAME || "feedbot";
const appPassword = process.env.MONGO_APP_PASSWORD || "password";
const appDbName = process.env.MONGO_APP_DB || "feed_database";

const appDb = db.getSiblingDB(appDbName);

if (!appDb.getUser(appUser)) {
  appDb.createUser({
    user: appUser,
    pwd: appPassword,
    roles: [{ role: "readWrite", db: appDbName }],
  });
  print(`Created MongoDB user ${appUser} for database ${appDbName}`);
} else {
  print(`MongoDB user ${appUser} already exists for database ${appDbName}`);
}
